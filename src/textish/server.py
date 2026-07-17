"""
SSH server layer for textish.

Two asyncssh classes work together for every incoming connection:

- ``TextishSSHServer``        — one instance per TCP connection; handles auth
                                and participates in the shared session limit.
- ``TextishSSHServerSession`` — one instance per shell session (i.e. after the
                                client requests a PTY and a shell); owns that
                                client's Textual app instance.

Each SSH session gets an independent Textual app instance and driver while all
sessions share one interpreter and asyncio event loop.
"""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping

import asyncssh

from .inprocess import InProcessAppSession
from .inprocess.session import AppSource

log = logging.getLogger("textish")

_MAX_OUTPUT_BUFFER = 256 * 1024


class SessionManager:
    """Tracks in-flight app session run tasks and orchestrates shutdown.

    Each ``TextishSSHServerSession`` registers its run task here on startup and
    the task is automatically removed when it completes. On server shutdown,
    ``close_all`` cancels every tracked task and awaits full cleanup, ensuring no
    app instances are left running.
    """

    def __init__(self, max_startups: int = 4) -> None:
        if max_startups < 1:
            raise ValueError("max_startups must be at least 1")
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_sessions = 0
        self._startup_slots = asyncio.Semaphore(max_startups)

    @property
    def active_sessions(self) -> int:
        """Number of reserved or running SSH app sessions."""
        return self._active_sessions

    def try_acquire(self, limit: int) -> bool:
        """Reserve a session slot, respecting *limit* (zero means unlimited)."""
        if limit > 0 and self._active_sessions >= limit:
            return False
        self._active_sessions += 1
        return True

    def release(self) -> None:
        """Release one previously acquired session slot."""
        if self._active_sessions > 0:
            self._active_sessions -= 1

    def add(self, task: asyncio.Task[None]) -> None:
        """Register a run task and observe its result when it finishes."""
        self._tasks.add(task)

        def task_done(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            self.release()
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                log.error("App session task failed", exc_info=exc)

        task.add_done_callback(task_done)

    async def run_app(self, session: InProcessAppSession) -> None:
        """Start an app without letting connection bursts starve the event loop."""
        await self._startup_slots.acquire()
        released = False

        def release_startup_slot() -> None:
            nonlocal released
            if not released:
                released = True
                self._startup_slots.release()

        try:
            await session.run(release_startup_slot)
        finally:
            release_startup_slot()

    async def close_all(self) -> None:
        """Cancel all tracked tasks and wait for them to finish.

        Cancellation triggers each task's ``finally`` block, which stops the
        app instance and closes its SSH channel.
        """
        closing = set(self._tasks)
        for task in closing:
            task.cancel()
        await asyncio.gather(*closing, return_exceptions=True)


class TextishSSHServerSession(asyncssh.SSHServerSession[bytes]):
    """Bridges one SSH PTY shell session to an independent app instance.

    asyncssh calls the methods on this class in response to SSH protocol events.
    Once a PTY has been negotiated, the session creates a
    :class:`~textish.inprocess.InProcessAppSession` and routes data between the
    SSH channel and its app instance.
    """

    def __init__(
        self,
        app_source: AppSource,
        session_manager: SessionManager,
        idle_timeout: float = 0,
    ) -> None:
        """
        Args:
            app_source:      App factory or import path used for each session.
            session_manager: Shared manager that tracks run tasks for shutdown.
            idle_timeout:    Seconds without client input before disconnecting.
        """
        self._app_source = app_source
        self._session_manager = session_manager
        self._idle_timeout = idle_timeout
        self._channel: asyncssh.SSHServerChannel[bytes] | None = None
        self._app_session: InProcessAppSession | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._cols: int = 80
        self._rows: int = 24
        self._has_pty: bool = False
        self._owns_slot = True
        self._idle_handle: asyncio.TimerHandle | None = None

    def connection_made(self, chan: asyncssh.SSHServerChannel[bytes]) -> None:
        """Called by asyncssh when the SSH channel is established."""
        self._channel = chan
        chan.set_write_buffer_limits(high=_MAX_OUTPUT_BUFFER)
        self._touch_idle_timer()
        log.debug("Channel opened")

    def _touch_idle_timer(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        if self._idle_timeout > 0:
            self._idle_handle = asyncio.get_running_loop().call_later(
                self._idle_timeout, self._idle_disconnect
            )

    def _idle_disconnect(self) -> None:
        log.info("Closing idle SSH session")
        self._cancel_app()
        if self._channel is not None:
            self._channel.close()

    def pause_writing(self) -> None:
        """Disconnect a slow client instead of buffering output without bound."""
        log.warning("Closing slow SSH session after output buffer limit")
        self._cancel_app()
        if self._channel is not None:
            self._channel.close()

    def pty_requested(
        self,
        term_type: str,
        term_size: tuple[int, int, int, int],
        _term_modes: Mapping[int, int],
    ) -> bool:
        """Called by asyncssh when the client requests a pseudo-terminal.

        Stores the initial terminal dimensions and returns ``True`` to approve.
        textish requires a PTY — without one the app cannot render correctly.
        """
        self._cols, self._rows = term_size[0], term_size[1]
        self._has_pty = True
        return True

    def shell_requested(self) -> bool:
        """Called by asyncssh when the client requests an interactive shell."""
        return True

    def session_started(self) -> None:
        """Called by asyncssh when the channel is fully open and ready.

        Rejects non-PTY connections with an error message. For valid PTY
        sessions, creates the app instance and starts its run loop.
        """
        assert self._channel is not None  # set by connection_made first
        if not self._has_pty:
            self._channel.write(b"textish requires an interactive terminal (PTY).\r\n")
            self._channel.close()
            self._release_slot()
            return
        try:
            self._app_session = InProcessAppSession(
                self._app_source,
                self._channel,
                cols=self._cols,
                rows=self._rows,
            )
            self._run_task = asyncio.create_task(
                self._session_manager.run_app(self._app_session)
            )
        except BaseException:
            self._release_slot()
            raise
        self._session_manager.add(self._run_task)
        self._owns_slot = False

    def data_received(self, data: bytes, datatype: int | None) -> None:
        """Called by asyncssh for each chunk of data from the SSH client."""
        if self._app_session is not None:
            self._app_session.send_input(data)
        self._touch_idle_timer()

    def terminal_size_changed(
        self, width: int, height: int, pixwidth: int, pixheight: int
    ) -> None:
        """Called by asyncssh when the client terminal is resized."""
        self._cols, self._rows = width, height
        self._touch_idle_timer()
        if self._app_session is not None:
            self._app_session.resize(width, height)

    def _release_slot(self) -> None:
        if self._owns_slot:
            self._session_manager.release()
            self._owns_slot = False

    def _cancel_app(self) -> bool:
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            return True
        return False

    def eof_received(self) -> bool:
        """Called by asyncssh when the client sends EOF (e.g. Ctrl+D)."""
        cleanup_pending = self._cancel_app()
        self._release_slot()
        # Keep the output side open until Textual restores terminal mode and the
        # app task closes the channel.
        return cleanup_pending

    def connection_lost(self, exc: Exception | None) -> None:
        """Called by asyncssh when the TCP connection drops."""
        if exc:
            log.warning("Connection lost with error: %s", exc)
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        self._cancel_app()
        self._release_slot()


class TextishSSHServer(asyncssh.SSHServer):
    """Handles the SSH connection layer — authentication and connection limits.

    asyncssh instantiates one of these per incoming TCP connection (via the
    factory in :func:`~textish.serve`). All instances share a ``SessionManager``
    so the configured session limit applies across every SSH connection.
    """

    def __init__(
        self,
        app_source: AppSource,
        max_connections: int,
        session_manager: SessionManager,
        auth_function: Callable[[str, str], bool | Awaitable[bool]] | None = None,
        idle_timeout: float = 0,
    ) -> None:
        """
        Args:
            app_source:         App factory or import path for each session.
            max_connections:    Maximum simultaneous sessions; ``0`` = unlimited.
            session_manager:    Shared manager that tracks run tasks for shutdown.
            auth_function:      Optional public-key validator. ``None`` allows
                                all connections without authentication.
            idle_timeout:       Seconds without client input before disconnecting.
        """
        self._app_source = app_source
        self._max_connections = max_connections
        self._session_manager = session_manager
        self._conn: asyncssh.SSHServerConnection | None = None
        self._auth_function = auth_function
        self._idle_timeout = idle_timeout

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        """Called by asyncssh when a new TCP connection is established.

        Session limits are enforced when each SSH session channel is requested.
        """
        self._conn = conn
        log.info("Connection from %s", conn.get_extra_info("peername"))

    def begin_auth(self, username: str) -> bool:
        """Return ``True`` (auth required) only when an auth function is set."""
        return self._auth_function is not None

    def session_requested(
        self,
    ) -> (
        tuple[asyncssh.SSHServerChannel[bytes], asyncssh.SSHServerSession[bytes]] | bool
    ):
        """Called by asyncssh when the client requests a shell session.

        Creates the raw-bytes channel (``encoding=None`` so the driver can write
        the terminal byte stream directly) and a fresh session handler.
        """
        assert self._conn is not None  # set by connection_made first
        if not self._session_manager.try_acquire(self._max_connections):
            log.warning(
                "Maximum sessions exceeded. Rejecting session from %s",
                self._conn.get_extra_info("peername"),
            )
            return False
        try:
            channel = self._conn.create_server_channel(encoding=None)
        except BaseException:
            self._session_manager.release()
            raise
        session = TextishSSHServerSession(
            self._app_source, self._session_manager, self._idle_timeout
        )
        return channel, session

    def public_key_auth_supported(self) -> bool:
        """Advertise public-key auth only when a validator is configured."""
        return self._auth_function is not None

    async def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        """Validate a client's public key via the user-supplied auth function."""
        assert self._auth_function is not None  # only called when advertised
        public_key_str = key.export_public_key().decode().strip()
        result = self._auth_function(username, public_key_str)
        if inspect.isawaitable(result):
            result = await result
        return result

    def connection_lost(self, exc: Exception | None) -> None:
        """Called by asyncssh when the TCP connection closes."""
        peer = self._conn.get_extra_info("peername") if self._conn else None
        if exc:
            log.warning("Connection from %s lost with error: %s", peer, exc)
        else:
            log.info("Connection closed from %s", peer)

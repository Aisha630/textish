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
from .metrics import MetricsSnapshot, ServerMetrics

log = logging.getLogger("textish")

_DEFAULT_OUTPUT_BUFFER_LIMIT = 256 * 1024
_DEFAULT_CHANNEL_WINDOW = 64 * 1024
_DEFAULT_MAX_TERMINAL_WIDTH = 240
_DEFAULT_MAX_TERMINAL_HEIGHT = 80


class SessionManager:
    """Tracks in-flight app session run tasks and orchestrates shutdown.

    Each ``TextishSSHServerSession`` registers its run task here on startup and
    the task is automatically removed when it completes. On server shutdown,
    ``close_all`` cancels every tracked task and awaits full cleanup, ensuring no
    app instances are left running.
    """

    def __init__(
        self,
        max_startups: int = 4,
        max_pending_startups: int = 64,
        max_ssh_connections: int = 0,
        max_authenticating: int = 64,
    ) -> None:
        if max_startups < 1:
            raise ValueError("max_startups must be at least 1")
        if max_pending_startups < max_startups:
            raise ValueError(
                "max_pending_startups must be greater than or equal to max_startups"
            )
        if max_ssh_connections < 0:
            raise ValueError("max_ssh_connections must be non-negative")
        if max_authenticating < 1:
            raise ValueError("max_authenticating must be at least 1")
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_sessions = 0
        self._ssh_connections = 0
        self._authenticating = 0
        self._pending_startups = 0
        self._max_pending_startups = max_pending_startups
        self._max_ssh_connections = max_ssh_connections
        self._max_authenticating = max_authenticating
        self._startup_slots = asyncio.Semaphore(max_startups)
        self.metrics = ServerMetrics()

    @property
    def active_sessions(self) -> int:
        """Number of reserved or running SSH app sessions."""
        return self._active_sessions

    @property
    def ssh_connections(self) -> int:
        """Number of accepted SSH transports."""
        return self._ssh_connections

    @property
    def authenticating(self) -> int:
        """Number of transports currently authenticating."""
        return self._authenticating

    @property
    def pending_startups(self) -> int:
        """Number of admitted apps which have not reached ready state."""
        return self._pending_startups

    def try_acquire_connection(self) -> bool:
        """Reserve an SSH transport slot."""
        if (
            self._max_ssh_connections > 0
            and self._ssh_connections >= self._max_ssh_connections
        ):
            self.metrics.reject_ssh_connection()
            return False
        self._ssh_connections += 1
        return True

    def release_connection(self) -> None:
        """Release one previously acquired SSH transport slot."""
        if self._ssh_connections > 0:
            self._ssh_connections -= 1

    def try_acquire_auth(self) -> bool:
        """Reserve an authentication slot."""
        if self._authenticating >= self._max_authenticating:
            self.metrics.reject_auth()
            return False
        self._authenticating += 1
        return True

    def release_auth(self) -> None:
        """Release one previously acquired authentication slot."""
        if self._authenticating > 0:
            self._authenticating -= 1

    def try_acquire(self, limit: int) -> bool:
        """Reserve a session slot, respecting *limit* (zero means unlimited)."""
        if limit > 0 and self._active_sessions >= limit:
            self.metrics.reject_session()
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
                self.metrics.app_failed()
                log.error("App session task failed", exc_info=exc)

        task.add_done_callback(task_done)

    def start_app(self, session: InProcessAppSession) -> asyncio.Task[None] | None:
        """Admit and schedule one app, or reject it when startup is saturated."""
        if self._pending_startups >= self._max_pending_startups:
            self.metrics.reject_startup()
            return None
        self._pending_startups += 1
        pending_released = False
        startup_started = asyncio.get_running_loop().time()

        def release_pending(ready: bool = False) -> None:
            nonlocal pending_released
            if not pending_released:
                pending_released = True
                self._pending_startups -= 1
                self.metrics.startup_finished(
                    asyncio.get_running_loop().time() - startup_started,
                    ready=ready,
                )

        task = asyncio.create_task(self.run_app(session, release_pending))
        task.add_done_callback(lambda _done: release_pending())
        self.add(task)
        return task

    async def run_app(
        self,
        session: InProcessAppSession,
        release_pending: Callable[[bool], None] | None = None,
    ) -> None:
        """Start an app without letting connection bursts starve the event loop."""
        await self._startup_slots.acquire()
        released = False

        def release_startup_slot(ready: bool = False) -> None:
            nonlocal released
            if not released:
                released = True
                self._startup_slots.release()
                if release_pending is not None:
                    release_pending(ready)

        try:
            await session.run(lambda: release_startup_slot(True))
        finally:
            release_startup_slot()

    def metrics_snapshot(self) -> MetricsSnapshot:
        """Return a point-in-time metrics snapshot for this worker."""
        return self.metrics.snapshot(
            ssh_connections=self._ssh_connections,
            authenticating=self._authenticating,
            active_sessions=self._active_sessions,
            pending_startups=self._pending_startups,
            app_tasks=len(self._tasks),
        )

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
        output_buffer_limit: int = _DEFAULT_OUTPUT_BUFFER_LIMIT,
        max_terminal_width: int = _DEFAULT_MAX_TERMINAL_WIDTH,
        max_terminal_height: int = _DEFAULT_MAX_TERMINAL_HEIGHT,
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
        self._output_buffer_limit = output_buffer_limit
        self._max_terminal_width = max_terminal_width
        self._max_terminal_height = max_terminal_height
        self._channel: asyncssh.SSHServerChannel[bytes] | None = None
        self._app_session: InProcessAppSession | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._cols: int = 80
        self._rows: int = 24
        self._has_pty: bool = False
        self._owns_slot = True
        self._idle_handle: asyncio.TimerHandle | None = None
        self._input_started_at: float | None = None

    def connection_made(self, chan: asyncssh.SSHServerChannel[bytes]) -> None:
        """Called by asyncssh when the SSH channel is established."""
        self._channel = chan
        chan.set_write_buffer_limits(high=self._output_buffer_limit)
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
        self._session_manager.metrics.disconnect_idle()
        self._cancel_app()
        if self._channel is not None:
            self._channel.close()

    def pause_writing(self) -> None:
        """Disconnect a slow client instead of buffering output without bound."""
        log.warning("Closing slow SSH session after output buffer limit")
        self._session_manager.metrics.disconnect_slow_reader()
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
        self._cols, self._rows = self._bounded_size(term_size[0], term_size[1])
        self._has_pty = True
        return True

    def _bounded_size(self, width: int, height: int) -> tuple[int, int]:
        """Clamp client-controlled dimensions to safe configured bounds."""
        return (
            min(max(width, 1), self._max_terminal_width),
            min(max(height, 1), self._max_terminal_height),
        )

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
                output_callback=self._output_written,
                error_callback=self._session_manager.metrics.app_failed,
            )
            self._run_task = self._session_manager.start_app(self._app_session)
            if self._run_task is None:
                self._channel.write(
                    b"textish is busy starting other sessions; try again.\r\n"
                )
                self._channel.close()
                self._release_slot()
                return
        except BaseException:
            self._release_slot()
            raise
        self._owns_slot = False

    def data_received(self, data: bytes, datatype: int | None) -> None:
        """Called by asyncssh for each chunk of data from the SSH client."""
        self._session_manager.metrics.add_input_bytes(len(data))
        if self._input_started_at is None:
            self._input_started_at = asyncio.get_running_loop().time()
        if self._app_session is not None:
            self._app_session.send_input(data)
        self._touch_idle_timer()

    def _output_written(self, count: int) -> None:
        self._session_manager.metrics.add_output_bytes(count)
        if self._input_started_at is not None:
            elapsed = asyncio.get_running_loop().time() - self._input_started_at
            self._session_manager.metrics.observe_input_render(elapsed)
            self._input_started_at = None

    def terminal_size_changed(
        self, width: int, height: int, pixwidth: int, pixheight: int
    ) -> None:
        """Called by asyncssh when the client terminal is resized."""
        self._cols, self._rows = self._bounded_size(width, height)
        self._touch_idle_timer()
        if self._app_session is not None:
            self._app_session.resize(self._cols, self._rows)

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
        channel_window: int = _DEFAULT_CHANNEL_WINDOW,
        output_buffer_limit: int = _DEFAULT_OUTPUT_BUFFER_LIMIT,
        max_terminal_width: int = _DEFAULT_MAX_TERMINAL_WIDTH,
        max_terminal_height: int = _DEFAULT_MAX_TERMINAL_HEIGHT,
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
        self._channel_window = channel_window
        self._output_buffer_limit = output_buffer_limit
        self._max_terminal_width = max_terminal_width
        self._max_terminal_height = max_terminal_height
        self._owns_connection_slot = False
        self._owns_auth_slot = False

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        """Called by asyncssh when a new TCP connection is established.

        Session limits are enforced when each SSH session channel is requested.
        """
        self._conn = conn
        if not self._session_manager.try_acquire_connection():
            log.warning(
                "Maximum SSH connections exceeded. Rejecting %s",
                conn.get_extra_info("peername"),
            )
            conn.abort()
            return
        self._owns_connection_slot = True
        log.info("Connection from %s", conn.get_extra_info("peername"))

    def begin_auth(self, username: str) -> bool:
        """Return ``True`` (auth required) only when an auth function is set."""
        if self._auth_function is None:
            return False
        if self._owns_auth_slot:
            return True
        if not self._session_manager.try_acquire_auth():
            log.warning("Authentication concurrency limit exceeded")
            if self._conn is not None:
                self._conn.abort()
            return True
        self._owns_auth_slot = True
        return True

    def auth_completed(self) -> None:
        """Release authentication admission once the client is accepted."""
        self._release_auth_slot()

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
        if not self._owns_connection_slot:
            return False
        if not self._session_manager.try_acquire(self._max_connections):
            log.warning(
                "Maximum sessions exceeded. Rejecting session from %s",
                self._conn.get_extra_info("peername"),
            )
            return False
        try:
            channel = self._conn.create_server_channel(
                encoding=None,
                window=self._channel_window,
                max_pktsize=min(self._channel_window, 32 * 1024),
            )
        except BaseException:
            self._session_manager.release()
            raise
        session = TextishSSHServerSession(
            self._app_source,
            self._session_manager,
            self._idle_timeout,
            self._output_buffer_limit,
            self._max_terminal_width,
            self._max_terminal_height,
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
        self._release_auth_slot()
        if self._owns_connection_slot:
            self._session_manager.release_connection()
            self._owns_connection_slot = False
        if exc:
            log.warning("Connection from %s lost with error: %s", peer, exc)
        else:
            log.info("Connection closed from %s", peer)

    def _release_auth_slot(self) -> None:
        if self._owns_auth_slot:
            self._session_manager.release_auth()
            self._owns_auth_slot = False

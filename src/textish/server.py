"""
SSH server layer for textish.

Two asyncssh classes work together for every incoming connection:

- ``TextishSSHServer``        — one instance per TCP connection; handles auth
                                and enforces the connection limit.
- ``TextishSSHServerSession`` — one instance per shell session (i.e. after the
                                client requests a PTY and a shell); owns the
                                subinterpreter-backed app session for that client.

Each connection's Textual app runs in its own subinterpreter (see
:mod:`textish.subinterp`), so the server process hosts many apps at once with
per-session isolation and, on Python 3.14+, real multi-core parallelism.
"""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable

import asyncssh

from .subinterp import SubinterpAppSession

log = logging.getLogger("textish")


class SessionManager:
    """Tracks in-flight app session run tasks and orchestrates shutdown.

    Each ``TextishSSHServerSession`` registers its run task here on startup and
    the task is automatically removed when it completes. On server shutdown,
    ``close_all`` cancels every tracked task and awaits full cleanup, ensuring no
    subinterpreters are left running.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def add(self, task: asyncio.Task[None]) -> None:
        """Register a run task. Automatically removed when the task finishes."""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close_all(self) -> None:
        """Cancel all tracked tasks and wait for them to finish.

        Cancellation triggers each task's ``finally`` block, which tears down
        the subinterpreter and closes its SSH channel.
        """
        closing = set(self._tasks)
        for task in closing:
            task.cancel()
        await asyncio.gather(*closing, return_exceptions=True)


class TextishSSHServerSession(asyncssh.SSHServerSession[bytes]):
    """Bridges one SSH PTY shell session to a subinterpreter-backed app.

    asyncssh calls the methods on this class in response to SSH protocol events.
    Once a PTY has been negotiated, the session creates a
    :class:`~textish.subinterp.SubinterpAppSession` and routes all data between
    the SSH channel and the app running in its subinterpreter.
    """

    def __init__(
        self,
        app_ref: str,
        session_manager: SessionManager,
        import_paths: tuple[str, ...] = (),
    ) -> None:
        """
        Args:
            app_ref:         Import path (``module:attr``) of the app to serve.
            session_manager: Shared manager that tracks run tasks for shutdown.
            import_paths:    Extra sys.path entries for the subinterpreter.
        """
        self._app_ref = app_ref
        self._session_manager = session_manager
        self._import_paths = import_paths
        self._channel: asyncssh.SSHServerChannel[bytes] | None = None
        self._app_session: SubinterpAppSession | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._cols: int = 80
        self._rows: int = 24
        self._has_pty: bool = False
        self._input_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._input_consumer: asyncio.Task[None] | None = None

    def connection_made(self, chan: asyncssh.SSHServerChannel[bytes]) -> None:
        """Called by asyncssh when the SSH channel is established."""
        self._channel = chan
        log.info("Channel opened")

    def pty_requested(
        self,
        term_type: str,
        term_size: tuple[int, int, int, int],
        _term_modes: dict[int, int],
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

    async def _consume_input(self) -> None:
        """Drain the input queue in order, forwarding each chunk to the app.

        Runs as a single consumer so chunks reach the app in arrival order.
        A ``None`` sentinel stops the loop once preceding data has been sent.
        """
        while True:
            data = await self._input_queue.get()
            if data is None:
                break
            if self._app_session is not None:
                await self._app_session.send_input(data)

    def session_started(self) -> None:
        """Called by asyncssh when the channel is fully open and ready.

        Rejects non-PTY connections with an error message. For valid PTY
        sessions, spawns the subinterpreter app session and starts its run loop.
        """
        assert self._channel is not None  # set by connection_made first
        if not self._has_pty:
            self._channel.write(b"textish requires an interactive terminal (PTY).\r\n")
            self._channel.close()
            return
        self._app_session = SubinterpAppSession(
            self._app_ref,
            self._channel,
            cols=self._cols,
            rows=self._rows,
            import_paths=self._import_paths,
        )
        self._run_task = asyncio.create_task(self._app_session.run())
        self._session_manager.add(self._run_task)
        self._input_consumer = asyncio.create_task(self._consume_input())

    def data_received(self, data: bytes, datatype: int | None) -> None:
        """Called by asyncssh for each chunk of data from the SSH client."""
        self._input_queue.put_nowait(data)

    def terminal_size_changed(
        self, width: int, height: int, pixwidth: int, pixheight: int
    ) -> None:
        """Called by asyncssh when the client terminal is resized."""
        self._cols, self._rows = width, height
        if self._app_session is not None:
            asyncio.create_task(self._app_session.resize(width, height))

    def eof_received(self) -> bool:
        """Called by asyncssh when the client sends EOF (e.g. Ctrl+D)."""
        if self._channel is not None:
            try:
                # Restore the client terminal from the alternate screen buffer.
                self._channel.write(b"\x1b[?1049l")
            except Exception:
                pass
        self._input_queue.put_nowait(None)
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
        return False

    def connection_lost(self, exc: Exception | None) -> None:
        """Called by asyncssh when the TCP connection drops."""
        if exc:
            log.warning("Connection lost with error: %s", exc)
        else:
            log.info("Connection closed")
        if self._input_consumer is not None and not self._input_consumer.done():
            self._input_consumer.cancel()
        # Cancelling the run task triggers its finally block, which tears down
        # the subinterpreter and closes the channel.
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()


class TextishSSHServer(asyncssh.SSHServer):
    """Handles the SSH connection layer — authentication and connection limits.

    asyncssh instantiates one of these per incoming TCP connection (via the
    factory in :func:`~textish.serve`). It shares the ``active_connections`` set
    with all sibling instances so the limit is enforced across all connections.
    """

    def __init__(
        self,
        app_ref: str,
        max_connections: int,
        active_connections: set[asyncssh.SSHServerConnection],
        session_manager: SessionManager,
        auth_function: Callable[[str, str], bool | Awaitable[bool]] | None = None,
        import_paths: tuple[str, ...] = (),
    ) -> None:
        """
        Args:
            app_ref:            App import path forwarded to each session.
            max_connections:    Maximum simultaneous sessions; ``0`` = unlimited.
            active_connections: Shared set tracked across all server instances.
            session_manager:    Shared manager that tracks run tasks for shutdown.
            auth_function:      Optional public-key validator. ``None`` allows
                                all connections without authentication.
            import_paths:       Extra sys.path entries for each subinterpreter.
        """
        self._app_ref: str = app_ref
        self._max_connections: int = max_connections
        self._active_connections: set[asyncssh.SSHServerConnection] = active_connections
        self._session_manager: SessionManager = session_manager
        self._conn: asyncssh.SSHServerConnection | None = None
        self._auth_function: Callable[[str, str], bool | Awaitable[bool]] | None = (
            auth_function
        )
        self._import_paths: tuple[str, ...] = import_paths

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        """Called by asyncssh when a new TCP connection is established.

        Enforces the connection limit before adding the connection to the
        active set. Rejected connections are closed immediately.
        """
        self._conn = conn
        if len(self._active_connections) >= self._max_connections > 0:
            log.warning(
                "Maximum connections exceeded. Closing new connection from %s",
                conn.get_extra_info("peername"),
            )
            conn.close()
            return
        self._active_connections.add(conn)
        log.info("Connection from %s", conn.get_extra_info("peername"))

    def begin_auth(self, username: str) -> bool:
        """Return ``True`` (auth required) only when an auth function is set."""
        return self._auth_function is not None

    def session_requested(
        self,
    ) -> tuple[asyncssh.SSHServerChannel[bytes], asyncssh.SSHServerSession[bytes]]:
        """Called by asyncssh when the client requests a shell session.

        Creates the raw-bytes channel (``encoding=None`` so the driver can write
        the terminal byte stream directly) and a fresh session handler.
        """
        assert self._conn is not None  # set by connection_made first
        channel = self._conn.create_server_channel(encoding=None)
        session = TextishSSHServerSession(
            self._app_ref, self._session_manager, self._import_paths
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
        self._active_connections.discard(self._conn)

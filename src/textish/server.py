"""
SSH server layer for textish.

Two asyncssh classes work together for every incoming connection:

- ``TextishSSHServer``        — one instance per TCP connection; handles auth
                                and enforces the connection limit.
- ``TextishSSHServerSession`` — one instance per shell session (i.e. after the
                                client requests a PTY and a shell); owns the
                                AppSession for that client.
"""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable

import asyncssh

from .app_session import AppSession

log = logging.getLogger("textish")


class TextishSSHServerSession(asyncssh.SSHServerSession):
    """Bridges one SSH PTY shell session to a Textual app subprocess.

    asyncssh calls the methods on this class in response to SSH protocol
    events. The session creates an :class:`~textish.app_session.AppSession`
    once a PTY has been negotiated, then routes all data between the SSH
    channel and the app.
    """

    def __init__(self, app_command: str) -> None:
        """
        Args:
            app_command: Shell command passed through to ``AppSession``.
        """
        self._app_command = app_command
        self._channel: asyncssh.SSHServerChannel | None = None
        self._app_session: AppSession | None = None
        self._cols: int = 80
        self._rows: int = 24
        self._has_pty: bool = False

    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        """Called by asyncssh when the SSH channel is established."""
        self._channel = chan
        log.info("Channel opened")

    def pty_requested(
        self,
        _term_type: str,
        term_size: tuple[int, int, int, int],
        _term_modes: dict,
    ) -> bool:
        """Called by asyncssh when the client requests a pseudo-terminal.

        Stores the initial terminal dimensions and signals approval by
        returning ``True``. textish requires a PTY — without one the app
        cannot render correctly.
        """
        self._cols, self._rows = term_size[0], term_size[1]
        self._has_pty = True
        return True

    def shell_requested(self) -> bool:
        """Called by asyncssh when the client requests an interactive shell.

        Always approved; the actual subprocess is launched in
        :meth:`session_started` once both PTY and shell are confirmed.
        """
        return True

    def session_started(self) -> None:
        """Called by asyncssh when the channel is fully open and ready.

        Rejects non-PTY connections with an error message (e.g. clients
        that run ``ssh host -p 2222 some-command`` without allocating a TTY).
        For valid PTY sessions, spawns the AppSession and starts its run loop.
        """
        if not self._has_pty:
            self._channel.write(b"textish requires an interactive terminal (PTY).\r\n")
            self._channel.close()
            return
        self._app_session = AppSession(
            app_command=self._app_command,
            channel=self._channel,
            cols=self._cols,
            rows=self._rows,
        )
        asyncio.create_task(self._app_session.run())

    def data_received(self, data: bytes, datatype) -> None:
        """Called by asyncssh for each chunk of data from the SSH client.

        Forwards the raw bytes to the app as a display (``b"D"``) packet.
        """
        if self._app_session is not None:
            asyncio.create_task(self._app_session.send_input(data))

    def terminal_size_changed(
        self, width: int, height: int, pixwidth: int, pixheight: int
    ) -> None:
        """Called by asyncssh when the client terminal is resized.

        Notifies the app subprocess so it can reflow its layout.
        Pixel dimensions are reported by the client but not used by Textual.
        """
        self._cols, self._rows = width, height
        if self._app_session is not None:
            asyncio.create_task(self._app_session.resize(width, height))

    def eof_received(self) -> bool:
        """Called by asyncssh when the client sends EOF (e.g. Ctrl+D).

        Writes the "disable alternate screen" escape sequence so the client's
        terminal is restored, then closes the app session. Returning ``False``
        tells asyncssh to also close the channel.
        """
        if self._channel is not None:
            try:
                # Switch the client terminal back from the alternate screen
                # buffer to the normal screen before disconnecting.
                self._channel.write(b"\x1b[?1049l")
            except Exception:
                pass
        if self._app_session is not None:
            asyncio.create_task(self._app_session.close())
        return False

    def connection_lost(self, exc: Exception | None) -> None:
        """Called by asyncssh when the TCP connection drops.

        Ensures the app subprocess is cleaned up even on an unexpected
        disconnect. ``AppSession.close`` is idempotent, so calling it here
        after ``eof_received`` has already triggered it is safe.
        """
        if exc:
            log.warning("Connection lost with error: %s", exc)
        else:
            log.info("Connection closed")
        if self._app_session is not None:
            asyncio.create_task(self._app_session.close())


class TextishSSHServer(asyncssh.SSHServer):
    """Handles the SSH connection layer — authentication and connection limits.

    asyncssh instantiates one of these per incoming TCP connection (via the
    factory lambda in :func:`~textish.serve_async`). It shares the
    ``active_connections`` set with all sibling instances so the limit is
    enforced across all concurrent connections.
    """

    def __init__(
        self,
        app_command: str,
        max_connections: int,
        active_connections: set[asyncssh.SSHServerConnection],
        auth_function: Callable[[str, str], bool | Awaitable[bool]] | None = None,
    ) -> None:
        """
        Args:
            app_command:        Shell command forwarded to each AppSession.
            max_connections:    Maximum simultaneous sessions; ``0`` = unlimited.
            active_connections: Shared set tracked across all server instances.
            auth_function:      Optional public-key validator. ``None`` allows
                                all connections without authentication.
        """
        self._app_command: str = app_command
        self._max_connections: int = max_connections
        self._active_connections: set[asyncssh.SSHServerConnection] = active_connections
        self._conn: asyncssh.SSHServerConnection | None = None
        self._auth_function: Callable[[str, str], bool | Awaitable[bool]] | None = (
            auth_function
        )

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
        """Called by asyncssh to determine whether authentication is required.

        Returns ``True`` (auth required) only when an auth function is
        configured; returning ``False`` grants anonymous access.
        """
        return self._auth_function is not None

    def session_requested(self):
        """Called by asyncssh when the client requests a shell session.

        Creates the raw-bytes channel and a fresh session handler for this
        client. ``encoding=None`` keeps data as bytes so we can forward the
        binary packet protocol without any codec interference.
        """
        channel = self._conn.create_server_channel(encoding=None)
        session = TextishSSHServerSession(self._app_command)
        return channel, session

    def public_key_auth_supported(self):
        """Advertise public-key auth only when a validator is configured."""
        return self._auth_function is not None

    async def validate_public_key(self, username, key):
        """Called by asyncssh to validate a client's public key.

        Exports the key to OpenSSH format and delegates to the user-supplied
        auth function, which may be sync or async.
        """
        public_key_str = key.export_public_key().decode().strip()
        result = self._auth_function(username, public_key_str)
        if inspect.isawaitable(result):
            result = await result
        return result

    def connection_lost(self, exc):
        """Called by asyncssh when the TCP connection closes.

        Removes the connection from the active set. ``discard`` is used
        instead of ``remove`` because connections that were rejected in
        ``connection_made`` were never added.
        """
        self._active_connections.discard(self._conn)

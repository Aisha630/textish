"""
SSH server that listens for incoming connections, creates a new AppSession
for each connection, and bridges data between it and the SSH channel.
"""

import asyncio
import asyncssh
import logging

from .app_session import AppSession

log = logging.getLogger("textish")


class TextishSSHServerSession(asyncssh.SSHServerSession):
    """Handles one PTY shell session for a connected client."""

    def __init__(self, app_command: str) -> None:
        self._app_command = app_command
        self._channel: asyncssh.SSHServerChannel | None = None
        self._app_session: AppSession | None = None
        self._cols: int = 80
        self._rows: int = 24
        self._has_pty: bool = False

    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        self._channel = chan
        log.info("Channel opened")

    def pty_requested(
        self,
        _term_type: str,
        term_size: tuple[int, int, int, int],
        _term_modes: dict,
    ) -> bool:
        self._cols, self._rows = term_size[0], term_size[1]
        self._has_pty = True
        return True

    def shell_requested(self) -> bool:
        return True

    def session_started(self) -> None:
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
        asyncio.get_running_loop().create_task(self._app_session.run())

    def data_received(self, data: bytes, datatype) -> None:
        if self._app_session is not None:
            asyncio.get_running_loop().create_task(self._app_session.send_input(data))

    def terminal_size_changed(
        self, width: int, height: int, pixwidth: int, pixheight: int
    ) -> None:
        self._cols, self._rows = width, height
        if self._app_session is not None:
            asyncio.get_running_loop().create_task(
                self._app_session.resize(width, height)
            )

    def eof_received(self) -> bool:
        if self._channel is not None:
            try:
                self._channel.write(b"\x1b[?1049l")
            except Exception:
                pass
        if self._app_session is not None:
            asyncio.get_running_loop().create_task(self._app_session.close())
        return False

    def connection_lost(self, _exc: Exception | None) -> None:
        if _exc:
            log.warning("Connection lost with error: %s", _exc)
        else:
            log.info("Connection closed")
        if self._app_session is not None:
            asyncio.get_running_loop().create_task(self._app_session.close())


class TextishSSHServer(asyncssh.SSHServer):
    """Handles the SSH connection itself — auth and session creation."""

    def __init__(self, app_command: str) -> None:
        self._app_command = app_command
        self._conn: asyncssh.SSHServerConnection | None = None

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self._conn = conn
        log.info("Connection from %s", conn.get_extra_info("peername"))

    def begin_auth(self, username: str) -> bool:
        return False  # no authentication required

    def session_requested(self):
        channel = self._conn.create_server_channel(encoding=None)
        session = TextishSSHServerSession(self._app_command)
        return channel, session

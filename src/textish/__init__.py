"""
textish — serve Textual apps over SSH.

Each incoming SSH connection spawns the Textual app as a fresh subprocess and
bridges the SSH channel to a server-side pseudo-terminal.

Quickstart:

    import asyncio
    from textish import AppConfig, serve

    asyncio.run(serve(AppConfig(app_command="python my_app.py", port=2222)))
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import asyncssh

from .config import AppConfig
from .server import SessionManager, TextishSSHServer

log = logging.getLogger("textish")


async def serve(config: AppConfig) -> None:
    """Start the SSH server and serve a Textual app to connecting clients.

    Runs until cancelled.

    Args:
        config: Validated server configuration.
    """
    # Track connections for graceful shutdown and max_connections enforcement.
    active_connections: set[asyncssh.SSHServerConnection] = set()
    session_manager = SessionManager()

    server = await asyncssh.create_server(
        lambda: TextishSSHServer(
            config.app_command,
            max_connections=config.max_connections,
            active_connections=active_connections,
            session_manager=session_manager,
            auth_function=config.auth,
            env=config.env,
        ),
        config.host,
        config.port,
        server_host_keys=list(config.host_keys),
    )
    async with server:
        try:
            await server.serve_forever()
        finally:
            await session_manager.close_all()


def authorized_keys(path: str | Path) -> Callable[[str, str], Awaitable[bool]]:
    """Return an auth function that allows connections whose public key appears
    in an OpenSSH ``authorized_keys`` file.

    The returned callable is compatible with `AppConfig.auth`.

    The file is re-read on every authentication attempt so changes take effect
    without restarting the server.

    Args:
        path: Path to the ``authorized_keys`` file (``~`` is expanded).

    Example::

        config = AppConfig(
            app_command="python my_app.py",
            auth=authorized_keys("~/.ssh/authorized_keys"),
        )
        await serve(config)
    """
    resolved = Path(path).expanduser()

    async def _auth(_username: str, public_key_str: str) -> bool:
        try:
            text = await asyncio.to_thread(resolved.read_text)
        except OSError:
            log.warning("Could not read authorized_keys file: %s", resolved)
            return False

        # The key blob (second whitespace-separated field) is the canonical
        # identity of the key
        parts = public_key_str.split()
        if len(parts) < 2:
            return False
        incoming_blob = parts[1]

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) >= 2 and fields[1] == incoming_blob:
                return True
        return False

    return _auth


__all__ = ["serve", "AppConfig", "authorized_keys"]

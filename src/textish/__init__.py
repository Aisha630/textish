"""
textish — serve Textual apps over SSH.

Each incoming SSH connection spawns the Textual app as a fresh subprocess and
bridges the SSH channel to the subprocess's stdin/stdout via Textual's
WebDriver packet protocol. The app has no idea it's running over SSH.

Quickstart::

    from textish import serve
    serve("python my_app.py", port=2222)
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import asyncssh

from .config import AppConfig
from .server import TextishSSHServer


def serve_config(config: AppConfig) -> None:
    """Start the SSH server from an :class:`~textish.config.AppConfig` instance.

    Convenience wrapper around :func:`serve` for callers that prefer to build
    configuration as an object rather than passing keyword arguments.
    """
    serve(
        app_command=config.app_command,
        host=config.host,
        port=config.port,
        host_keys=(config.host_key_path,) if config.host_key_path else None,
        max_connections=config.max_connections,
        auth_function=config.auth,
    )


async def serve_async(
    app_command: str,
    host: str = "0.0.0.0",
    port: int = 2222,
    host_keys: tuple[str, ...] | list[str] | None = None,
    max_connections: int = 0,
    auth_function: Callable[[str, str], bool | Awaitable[bool]] | None = None,
) -> None:
    """Async version of :func:`serve` for use inside a running event loop.

    Args:
        app_command:     Shell command that launches the Textual app.
        host:            Address to listen on. Defaults to all interfaces.
        port:            TCP port to listen on. Defaults to 2222.
        host_keys:       Paths to SSH host key files. Defaults to
                         ``~/.ssh/ssh_host_key``.
        max_connections: Maximum simultaneous sessions. ``0`` = unlimited.
        auth_function:   Optional public-key validator with signature
                         ``(username, public_key_str) -> bool``. May be async.
                         Pass ``None`` to allow all connections.
    """
    if host_keys is None:
        host_keys = (str(Path("~/.ssh/ssh_host_key").expanduser()),)

    active_connections: set[asyncssh.SSHServerConnection] = set()

    server = await asyncssh.create_server(
        lambda: TextishSSHServer(
            app_command,
            max_connections=max_connections,
            active_connections=active_connections,
            auth_function=auth_function,
        ),
        host,
        port,
        server_host_keys=list(host_keys),
    )
    async with server:
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            for conn in set(active_connections):
                conn.close()
            # Brief pause to let asyncssh flush close frames before the event
            # loop shuts down. There is no asyncssh API to await full teardown.
            await asyncio.sleep(0.1)


def serve(
    app_command: str,
    host: str = "0.0.0.0",
    port: int = 2222,
    host_keys: tuple[str, ...] | list[str] | None = None,
    max_connections: int = 0,
    auth_function: Callable[[str, str], bool | Awaitable[bool]] | None = None,
) -> None:
    """Start the SSH server and serve a Textual app to connecting clients.

    Blocks until interrupted (e.g. ``Ctrl+C``). For embedding inside an
    existing event loop, use :func:`serve_async` instead.

    Args:
        app_command:     Shell command that launches the Textual app,
                         e.g. ``"python my_app.py"``.
        host:            Address to listen on. Defaults to all interfaces.
        port:            TCP port to listen on. Defaults to 2222.
        host_keys:       Paths to SSH host key files. Generate one with:
                         ``ssh-keygen -t ed25519 -f ssh_host_key -N ""``.
                         Defaults to ``~/.ssh/ssh_host_key``.
        max_connections: Maximum simultaneous sessions. ``0`` = unlimited.
        auth_function:   Optional public-key validator with signature
                         ``(username, public_key_str) -> bool``. May be async.
                         Pass ``None`` to allow all connections.
    """
    asyncio.run(
        serve_async(app_command, host, port, host_keys, max_connections, auth_function)
    )


__all__ = ["serve", "serve_async", "serve_config", "AppConfig"]

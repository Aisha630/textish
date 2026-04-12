"""
textish — serve Textual apps over SSH.

Usage::

    from textish import serve
    serve("python my_app.py", port=2222)
"""

## The main idea: Textual's WebDriver runs apps in a subprocess and communicates
## over pipes using a simple packet protocol. We reuse that exact mechanism —
## each SSH connection spawns the app as a subprocess, and we bridge the SSH
## channel to the subprocess's stdin/stdout. No Textual internals are modified.

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import asyncssh

from .config import AppConfig
from .server import TextishSSHServer


def serve_config(config: AppConfig) -> None:
    """Start the SSH server using an AppConfig object."""
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
    """Async version of serve(). Use this if you're already inside an event loop."""
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
            await asyncio.sleep(0.1)  # give connections a moment to close


def serve(
    app_command: str,
    host: str = "0.0.0.0",
    port: int = 2222,
    host_keys: tuple[str, ...] | list[str] | None = None,
    max_connections: int = 0,
    auth_function: Callable[[str, str], bool | Awaitable[bool]] | None = None,
) -> None:
    """Start the SSH server and serve a Textual app to connecting clients.

    Args:
        app_command: Shell command to run the app, e.g. ``"python my_app.py"``.
        host:        Host to listen on. Defaults to all interfaces.
        port:        Port to listen on. Defaults to 2222.
        host_keys:   Paths to SSH host key files. Generate one with:
                     ``ssh-keygen -t ed25519 -f ssh_host_key``
    """
    asyncio.run(
        serve_async(app_command, host, port, host_keys, max_connections, auth_function)
    )


__all__ = ["serve", "serve_async", "serve_config"]

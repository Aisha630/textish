"""
textish — serve Textual apps over SSH.

Usage::

    from textish import serve
    serve("python my_app.py", port=2222)
"""

import asyncio
import asyncssh

from .server import TextishSSHServer


async def serve_async(
    app_command: str,
    host: str = "0.0.0.0",
    port: int = 2222,
    host_keys: tuple[str, ...] | list[str] = ("ssh_host_key",),
) -> None:
    """Async version of serve(). Use this if you're already inside an event loop."""
    server = await asyncssh.create_server(
        lambda: TextishSSHServer(app_command),
        host,
        port,
        server_host_keys=list(host_keys),
    )
    async with server:
        await server.serve_forever()


def serve(
    app_command: str,
    host: str = "0.0.0.0",
    port: int = 2222,
    host_keys: tuple[str, ...] | list[str] = ("ssh_host_key",),
) -> None:
    """Start the SSH server and serve a Textual app to connecting clients.

    Args:
        app_command: Shell command to run the Textual app, e.g. ``"python my_app.py"``.
        host:        Host to listen on. Defaults to all interfaces.
        port:        Port to listen on. Defaults to 2222.
        host_keys:   Paths to SSH host key files. Generate one with:
                     ``ssh-keygen -t ed25519 -f ssh_host_key``
    """
    asyncio.run(serve_async(app_command, host, port, host_keys))


__all__ = ["serve", "serve_async"]

"""
textish — serve Textual apps over SSH.

Usage::

    from textual.app import App, ComposeResult
    from textual.widgets import Static

    class MyApp(App):
        def compose(self) -> ComposeResult:
            yield Static("Hello over SSH!")

    from textish import serve
    serve(MyApp, port=2222)
"""

## The main issue with implementing something like this was that Textual apps expect to be run in a terminal, and they use ANSI escape codes to control the screen. When you SSH into a server, you get a terminal session, but it's not a real terminal — it's just a stream of bytes. So we need to create a pseudo-terminal (PTY) for each SSH session and run the Textual app in that PTY. Then we can read the output from the PTY and send it back to the SSH client, and vice versa.

## More importantly, this set up is based on the fact that Textual's WebDriver (which is used for testing) also runs Textual apps in a subprocess and communicates with them over pipes. So we can reuse a lot of the same logic to create a similar setup for SSH sessions.

## This choice also means that we don't have to modify Textual at all — we can just run the app as a subprocess and let it do its thing, while we handle the SSH communication separately.

## It also does have its downsides. Running each app in a separate subprocess means that we have more overhead compared to running everything in the same process. It also means that we can't easily share state between different SSH sessions, since each one is running in its own process. But for many use cases, this tradeoff is worth it for the simplicity and compatibility it provides.

import asyncio
import asyncssh
from .server import TextishSSHServer
from textual.app import App


async def serve_async(
    app_class: "type[App]",
    host: str = "0.0.0.0",
    port: int = 2222,
    host_keys: tuple[str, ...] | list[str] = ("ssh_host_key",),
) -> None:
    """Async version of serve(). Use this if you're already inside an event loop."""
    server = await asyncssh.create_server(
        lambda: TextishSSHServer(app_class),
        host,
        port,
        server_host_keys=list(host_keys),
    )
    async with server:
        await server.serve_forever()


def serve(
    app_class: "type[App]",
    host: str = "0.0.0.0",
    port: int = 2222,
    host_keys: tuple[str, ...] | list[str] = ("~/.ssh/ssh_host_key",),
) -> None:
    """Start the SSH server and serve a Textual app to connecting clients.

    Args:
        app_class:  The Textual App class to serve (not an instance).
        host:       Host to listen on. Defaults to all interfaces.
        port:       Port to listen on. Defaults to 2222.
        host_keys:  Paths to SSH host key files. Generate one with:
                    ``ssh-keygen -t ed25519 -f ssh_host_key``
    """

    asyncio.run(serve_async(app_class, host, port, host_keys))


__all__ = ["serve", "serve_async"]

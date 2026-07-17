"""End-to-end tests for the shared-interpreter SSH server."""

from __future__ import annotations

import asyncio
from typing import Any

import asyncssh
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from textish.server import SessionManager, TextishSSHServer

pytestmark = pytest.mark.asyncio


class EchoApp(App[Any]):
    """Show a banner and echo input through the complete SSH bridge."""

    def compose(self) -> ComposeResult:
        yield Static("SHARED-BANNER")
        yield Static("", id="echo")
        yield Input(id="input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#echo", Static).update(f"echo:{event.value}")


@pytest.fixture
async def server_port(tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    key_path = tmp_path / "host_key"
    key.write_private_key(str(key_path))

    manager = SessionManager()
    server = await asyncssh.create_server(
        lambda: TextishSSHServer(
            EchoApp,
            max_connections=0,
            session_manager=manager,
        ),
        "127.0.0.1",
        0,
        server_host_keys=[str(key_path)],
    )
    async with server:
        yield server.get_port()
        await manager.close_all()


async def _read_until(stream, needle: bytes, timeout: float = 15.0) -> bytes:
    buf = bytearray()

    async def _pump() -> None:
        while needle not in buf:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buf.extend(
                chunk.encode("utf-8", "replace") if isinstance(chunk, str) else chunk
            )

    try:
        await asyncio.wait_for(_pump(), timeout)
    except TimeoutError:
        pass
    return bytes(buf)


async def test_app_renders_over_ssh(server_port):
    async with asyncssh.connect(
        "127.0.0.1", server_port, known_hosts=None, username="t"
    ) as conn:
        proc = await conn.create_process(term_type="xterm-256color", term_size=(80, 24))
        out = await _read_until(proc.stdout, b"SHARED-BANNER")
        assert b"SHARED-BANNER" in out
        assert b"\x1b[?1049h" in out  # entered alternate screen
        proc.close()


async def test_input_delivered_to_app(server_port):
    async with asyncssh.connect(
        "127.0.0.1", server_port, known_hosts=None, username="t"
    ) as conn:
        proc = await conn.create_process(term_type="xterm-256color", term_size=(80, 24))
        await _read_until(proc.stdout, b"SHARED-BANNER")
        proc.stdin.write("hey")
        out = await _read_until(proc.stdout, b"echo:hey")
        assert b"echo:hey" in out
        proc.close()


async def test_non_pty_connection_is_rejected(server_port):
    async with asyncssh.connect(
        "127.0.0.1", server_port, known_hosts=None, username="t"
    ) as conn:
        # No term_type => no PTY; the server should reject with a message.
        result = await conn.run("", check=False)
        assert "requires an interactive terminal" in (result.stdout or "")


async def test_concurrent_sessions_have_independent_apps(server_port):
    """Several clients receive independent apps in one interpreter."""

    async def one(value: str) -> bool:
        async with asyncssh.connect(
            "127.0.0.1", server_port, known_hosts=None, username="t"
        ) as conn:
            proc = await conn.create_process(
                term_type="xterm-256color", term_size=(80, 24)
            )
            out = await _read_until(proc.stdout, b"SHARED-BANNER")
            proc.stdin.write(value)
            echoed = await _read_until(proc.stdout, f"echo:{value}".encode())
            proc.close()
            return b"SHARED-BANNER" in out and f"echo:{value}".encode() in echoed

    results = await asyncio.gather(*(one(f"user-{index}") for index in range(5)))
    assert all(results)

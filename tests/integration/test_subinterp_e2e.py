"""End-to-end tests for the textish SSH server (subinterpreter backend).

Skipped automatically on Python < 3.14, where ``concurrent.interpreters`` is not
available. On 3.14+ these start a real ``TextishSSHServer``, connect a real
asyncssh client requesting a PTY, and assert on the rendered byte stream. Each
client's app runs in its own subinterpreter.
"""

from __future__ import annotations

import asyncio
import socket

import asyncssh
import pytest

from textish.server import SessionManager, TextishSSHServer
from textish.subinterp import SUBINTERP_AVAILABLE

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not SUBINTERP_AVAILABLE,
        reason="requires Python 3.14+ (concurrent.interpreters)",
    ),
]

APP_REF = "textish.subinterp._demo_app:EchoApp"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def server_port(tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    key_path = tmp_path / "host_key"
    key.write_private_key(str(key_path))

    port = _free_port()
    manager = SessionManager()
    server = await asyncssh.create_server(
        lambda: TextishSSHServer(
            APP_REF,
            max_connections=0,
            active_connections=set(),
            session_manager=manager,
        ),
        "127.0.0.1",
        port,
        server_host_keys=[str(key_path)],
    )
    async with server:
        yield port
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


async def test_app_renders_over_ssh_in_subinterpreter(server_port):
    async with asyncssh.connect(
        "127.0.0.1", server_port, known_hosts=None, username="t"
    ) as conn:
        proc = await conn.create_process(term_type="xterm-256color", term_size=(80, 24))
        out = await _read_until(proc.stdout, b"SUBINTERP-BANNER")
        assert b"SUBINTERP-BANNER" in out
        assert b"\x1b[?1049h" in out  # entered alternate screen
        proc.close()


async def test_input_delivered_to_subinterpreter(server_port):
    async with asyncssh.connect(
        "127.0.0.1", server_port, known_hosts=None, username="t"
    ) as conn:
        proc = await conn.create_process(term_type="xterm-256color", term_size=(80, 24))
        await _read_until(proc.stdout, b"SUBINTERP-BANNER")
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


async def test_concurrent_subinterpreter_sessions(server_port):
    """Several clients, each in its own subinterpreter, served together."""

    async def one() -> bool:
        async with asyncssh.connect(
            "127.0.0.1", server_port, known_hosts=None, username="t"
        ) as conn:
            proc = await conn.create_process(
                term_type="xterm-256color", term_size=(80, 24)
            )
            out = await _read_until(proc.stdout, b"SUBINTERP-BANNER")
            proc.close()
            return b"SUBINTERP-BANNER" in out

    results = await asyncio.gather(*[one() for _ in range(5)])
    assert all(results)

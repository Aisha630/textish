"""End-to-end integration tests.

These tests spin up a real textish SSH server, connect to it with an asyncssh
client, and verify that a Textual app actually starts and renders output over
the wire. No mocking — this exercises the full stack.
"""

import asyncio
import socket
import sys
from textwrap import dedent

import asyncssh
import pytest

from textish.server import SessionManager, TextishSSHServer

_TEST_APP = dedent("""\
    from textual.app import App, ComposeResult
    from textual.widgets import Label

    class App_(App):
        def compose(self) -> ComposeResult:
            yield Label("TEXTISH_OK")

    App_().run()
""")


def _free_port() -> int:
    """Return an unused TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def ssh_server(tmp_path):
    """Start a textish server on a random port and yield (host, port).

    Uses asyncssh.create_server directly — the server is bound and listening
    as soon as the fixture yields, so no polling is needed.
    """
    port = _free_port()

    key = asyncssh.generate_private_key("ssh-ed25519")
    key_path = tmp_path / "host_key"
    key.write_private_key(str(key_path))

    app_path = tmp_path / "app.py"
    app_path.write_text(_TEST_APP)

    active_connections: set[asyncssh.SSHServerConnection] = set()
    session_manager = SessionManager()

    server = await asyncssh.create_server(
        lambda: TextishSSHServer(
            f"{sys.executable} {app_path}",
            max_connections=0,
            active_connections=active_connections,
            session_manager=session_manager,
        ),
        "127.0.0.1",
        port,
        server_host_keys=[str(key_path)],
    )

    async with server:
        yield "127.0.0.1", port
        await session_manager.close_all()


async def test_ssh_connect_receives_app_output(ssh_server):
    """Connecting over SSH should deliver rendered Textual output to the client."""
    host, port = ssh_server

    async with asyncssh.connect(
        host,
        port=port,
        known_hosts=None,
        username="test",
        client_keys=[],
    ) as conn:
        process = await conn.create_process(
            term_type="xterm",
            term_size=(80, 24),
            encoding=None,
        )
        data = b""
        try:
            async with asyncio.timeout(10.0):
                while b"TEXTISH_OK" not in data:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    data += chunk
        finally:
            process.close()

    assert b"TEXTISH_OK" in data, f"marker not found in server output: {data!r}"


async def test_multiple_clients_connect_independently(ssh_server):
    """Each SSH connection should get its own independent app instance."""
    host, port = ssh_server

    async def _collect(collected: list[bytes]) -> None:
        async with asyncssh.connect(
            host,
            port=port,
            known_hosts=None,
            username="test",
            client_keys=[],
        ) as conn:
            process = await conn.create_process(
                term_type="xterm",
                term_size=(80, 24),
                encoding=None,
            )
            data = b""
            try:
                async with asyncio.timeout(10.0):
                    while b"TEXTISH_OK" not in data:
                        chunk = await process.stdout.read(4096)
                        if not chunk:
                            break
                        data += chunk
            finally:
                process.close()
            collected.append(data)

    results: list[bytes] = []
    await asyncio.gather(_collect(results), _collect(results))

    assert len(results) == 2
    assert all(b"TEXTISH_OK" in r for r in results)


async def test_server_cleans_up_after_client_disconnects(ssh_server):
    """Disconnecting a client should not leave orphan processes behind."""
    psutil = pytest.importorskip("psutil")

    host, port = ssh_server

    current_process = psutil.Process()
    before = {p.pid for p in current_process.children(recursive=True)}

    async with asyncssh.connect(
        host,
        port=port,
        known_hosts=None,
        username="test",
        client_keys=[],
    ) as conn:
        process = await conn.create_process(
            term_type="xterm",
            term_size=(80, 24),
            encoding=None,
        )
        data = b""
        try:
            async with asyncio.timeout(10.0):
                while b"TEXTISH_OK" not in data:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    data += chunk
        finally:
            process.close()

    # Poll until all child processes spawned during the test are reaped.
    deadline = asyncio.get_running_loop().time() + 5.0
    while asyncio.get_running_loop().time() < deadline:
        after = {p.pid for p in current_process.children(recursive=True)}
        new_pids = after - before
        if not new_pids:
            break
        await asyncio.sleep(0.05)
    assert not new_pids, f"orphan processes after disconnect: {new_pids}"

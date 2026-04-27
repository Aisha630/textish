import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from textish import authorized_keys
from textish.server import SessionManager, TextishSSHServerSession


@pytest.mark.asyncio
async def test_pty_requested_stores_dimensions_and_returns_true():
    session = TextishSSHServerSession("cmd", SessionManager())
    result = session.pty_requested("xterm", (132, 50, 0, 0), {})
    assert result is True
    assert session._cols == 132
    assert session._rows == 50
    assert session._term_type == "xterm"
    assert session._has_pty is True


@pytest.mark.asyncio
async def test_session_started_without_pty_writes_error_and_closes(mock_channel):
    session = TextishSSHServerSession("cmd", SessionManager())
    session._channel = mock_channel

    session.session_started()

    mock_channel.write.assert_called_once_with(
        b"textish requires an interactive terminal (PTY).\r\n"
    )
    mock_channel.close.assert_called_once()
    assert session._app_session is None


@pytest.mark.asyncio
async def test_terminal_size_changed_calls_resize():
    session = TextishSSHServerSession("cmd", SessionManager())
    calls = []

    async def fake_resize(cols, rows):
        calls.append((cols, rows))

    mock_app_session = MagicMock()
    mock_app_session.resize = fake_resize
    session._app_session = mock_app_session

    session.terminal_size_changed(120, 40, 0, 0)
    await asyncio.sleep(0)  # give the event loop a chance to run the resize task
    assert calls == [(120, 40)]


@pytest.mark.asyncio
async def test_session_requested_returns_channel_and_correct_session_type(
    mock_ssh_conn, make_server
):
    server = make_server()
    mock_channel = MagicMock()
    mock_ssh_conn.create_server_channel.return_value = mock_channel
    server._conn = mock_ssh_conn

    channel, session = server.session_requested()

    assert channel is mock_channel
    assert isinstance(session, TextishSSHServerSession)
    assert session._app_command == "cmd"
    mock_ssh_conn.create_server_channel.assert_called_once_with(encoding=None)


@pytest.mark.asyncio
async def test_session_requested_forwards_env_configuration(mock_ssh_conn, make_server):
    server = make_server(env={"APP_ENV": "configured"})
    mock_channel = MagicMock()
    mock_ssh_conn.create_server_channel.return_value = mock_channel
    server._conn = mock_ssh_conn

    _channel, session = server.session_requested()

    assert session._env == {"APP_ENV": "configured"}


@pytest.mark.asyncio
async def test_connection_made_stores_connection(mock_ssh_conn, make_server):
    server = make_server()
    server.connection_made(mock_ssh_conn)
    assert server._conn is mock_ssh_conn


@pytest.mark.asyncio
async def test_authorized_keys_reads_file_off_event_loop(tmp_path):
    auth_file = tmp_path / "authorized_keys"
    auth_file.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest user@example\n")
    auth = authorized_keys(auth_file)

    with patch("textish.asyncio.to_thread", new=AsyncMock()) as to_thread:
        to_thread.return_value = auth_file.read_text()
        result = auth("user", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest")

        assert inspect.isawaitable(result)
        assert await result is True

    to_thread.assert_awaited_once()

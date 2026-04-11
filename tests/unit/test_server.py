import pytest
from unittest.mock import MagicMock
from textish.server import TextishSSHServer, TextishSSHServerSession


@pytest.mark.asyncio
async def test_pty_requested_stores_dimensions_and_returns_true():
    session = TextishSSHServerSession("cmd")
    result = session.pty_requested("xterm", (132, 50, 0, 0), {})
    assert result is True
    assert session._cols == 132
    assert session._rows == 50
    assert session._has_pty is True


@pytest.mark.asyncio
async def test_session_started_without_pty_writes_error_and_closes(mock_channel):
    session = TextishSSHServerSession("cmd")
    session._channel = mock_channel

    await session.session_started()

    mock_channel.write.assert_called_once_with(
        b"textish requires an interactive terminal (PTY).\r\n"
    )
    mock_channel.close.assert_called_once()
    assert session._app_session is None


@pytest.mark.asyncio
async def test_terminal_size_changed_calls_resize():
    session = TextishSSHServerSession("cmd")
    calls = []

    async def fake_resize(cols, rows):
        calls.append((cols, rows))

    mock_app_session = MagicMock()
    mock_app_session.resize = fake_resize
    session._app_session = mock_app_session

    session.terminal_size_changed(120, 40, 0, 0)

    assert calls == [(120, 40)]


@pytest.mark.asyncio
async def test_session_requested_returns_channel_and_correct_session_type(
    mock_ssh_conn,
):
    server = TextishSSHServer("cmd")
    mock_channel = MagicMock()
    mock_ssh_conn.create_server_channel.return_value = mock_channel
    server._conn = mock_ssh_conn

    channel, session = await server.session_requested()

    assert channel is mock_channel
    assert isinstance(session, TextishSSHServerSession)
    assert session._app_command == "cmd"
    mock_ssh_conn.create_server_channel.assert_called_once_with(encoding=None)


@pytest.mark.asyncio
async def test_connection_made_stores_connection(mock_ssh_conn):
    server = TextishSSHServer("cmd")
    server.connection_made(mock_ssh_conn)
    assert server._conn is mock_ssh_conn

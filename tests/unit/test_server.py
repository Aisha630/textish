import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest

from textish import authorized_keys
from textish.server import SessionManager, TextishSSHServer, TextishSSHServerSession


@pytest.mark.asyncio
async def test_pty_requested_stores_dimensions_and_returns_true():
    session = TextishSSHServerSession("pkg.mod:App", SessionManager())
    result = session.pty_requested("xterm", (132, 50, 0, 0), {})
    assert result is True
    assert session._cols == 132
    assert session._rows == 50
    assert session._has_pty is True


@pytest.mark.asyncio
async def test_session_started_without_pty_writes_error_and_closes(mock_channel):
    session = TextishSSHServerSession("pkg.mod:App", SessionManager())
    session._channel = mock_channel

    session.session_started()

    mock_channel.write.assert_called_once_with(
        b"textish requires an interactive terminal (PTY).\r\n"
    )
    mock_channel.close.assert_called_once()
    assert session._app_session is None


@pytest.mark.asyncio
async def test_terminal_size_changed_calls_resize():
    session = TextishSSHServerSession("pkg.mod:App", SessionManager())
    calls = []

    def fake_resize(cols, rows):
        calls.append((cols, rows))

    mock_app_session = MagicMock()
    mock_app_session.resize = fake_resize
    session._app_session = mock_app_session

    session.terminal_size_changed(120, 40, 0, 0)
    assert calls == [(120, 40)]


@pytest.mark.asyncio
async def test_session_requested_returns_channel_and_correct_session_type(
    mock_ssh_conn, make_server
):
    server = make_server()
    mock_channel = MagicMock()
    mock_ssh_conn.create_server_channel.return_value = mock_channel
    server.connection_made(mock_ssh_conn)

    channel, session = server.session_requested()

    assert channel is mock_channel
    assert isinstance(session, TextishSSHServerSession)
    assert session._app_source == "pkg.mod:App"
    mock_ssh_conn.create_server_channel.assert_called_once_with(
        encoding=None, window=64 * 1024, max_pktsize=32 * 1024
    )


@pytest.mark.asyncio
async def test_session_requested_forwards_app_ref(mock_ssh_conn, make_server):
    server = make_server(app_ref="my_pkg.my_mod:MyApp")
    mock_channel = MagicMock()
    mock_ssh_conn.create_server_channel.return_value = mock_channel
    server.connection_made(mock_ssh_conn)

    _channel, session = server.session_requested()

    assert session._app_source == "my_pkg.my_mod:MyApp"


@pytest.mark.asyncio
async def test_connection_made_stores_connection(mock_ssh_conn, make_server):
    server = make_server()
    server.connection_made(mock_ssh_conn)
    assert server._conn is mock_ssh_conn


def test_connection_limit_rejects_excess_transport(mock_ssh_conn):
    manager = SessionManager(max_ssh_connections=1)
    first = TextishSSHServer("pkg.mod:App", 0, manager)
    second = TextishSSHServer("pkg.mod:App", 0, manager)

    first.connection_made(mock_ssh_conn)
    second_conn = MagicMock()
    second_conn.get_extra_info.return_value = ("127.0.0.1", 54321)
    second.connection_made(second_conn)

    assert manager.ssh_connections == 1
    second_conn.abort.assert_called_once()
    assert manager.metrics_snapshot()["rejected_ssh_connections_total"] == 1

    first.connection_lost(None)
    assert manager.ssh_connections == 0


def test_authentication_limit_is_released_on_completion():
    manager = SessionManager(max_authenticating=1)
    first = TextishSSHServer("pkg.mod:App", 0, manager, auth_function=lambda *_: True)
    second = TextishSSHServer("pkg.mod:App", 0, manager, auth_function=lambda *_: True)
    first_conn = MagicMock()
    second_conn = MagicMock()
    first.connection_made(first_conn)
    second.connection_made(second_conn)

    assert first.begin_auth("first") is True
    assert manager.authenticating == 1
    assert second.begin_auth("second") is True
    second_conn.abort.assert_called_once()
    assert manager.metrics_snapshot()["rejected_auth_total"] == 1

    first.auth_completed()
    assert manager.authenticating == 0


@pytest.mark.asyncio
async def test_authorized_keys_reads_file_off_event_loop(tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    public_key = key.export_public_key().decode().strip()
    auth_file = tmp_path / "authorized_keys"
    auth_file.write_text(f"{public_key} user@example\n")
    auth = authorized_keys(auth_file)

    with patch("textish.asyncio.to_thread", new=AsyncMock()) as to_thread:
        to_thread.return_value = auth_file.read_text()
        result = auth("user", public_key)

        assert inspect.isawaitable(result)
        assert await result is True

    to_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_authorized_keys_accepts_restrict_option(tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    public_key = key.export_public_key().decode().strip()
    auth_file = tmp_path / "authorized_keys"
    auth_file.write_text(f"restrict {public_key} user@example\n")

    assert await authorized_keys(auth_file)("user", public_key) is True


def test_session_limit_counts_channels_not_connections(mock_ssh_conn):
    manager = SessionManager()
    server = TextishSSHServer("pkg.mod:App", max_connections=1, session_manager=manager)
    server.connection_made(mock_ssh_conn)

    assert server.session_requested() is not False
    assert server.session_requested() is False
    assert manager.active_sessions == 1
    assert manager.metrics_snapshot()["rejected_sessions_total"] == 1


@pytest.mark.asyncio
async def test_pending_startup_limit_rejects_and_releases_on_cancellation():
    manager = SessionManager(max_startups=1, max_pending_startups=1)
    app_started = asyncio.Event()

    class BlockingSession:
        async def run(self, ready_callback):
            app_started.set()
            await asyncio.Event().wait()

    assert manager.try_acquire(0)
    task = manager.start_app(BlockingSession())  # type: ignore[arg-type]
    assert task is not None
    await app_started.wait()
    assert manager.pending_startups == 1

    assert manager.try_acquire(0)
    assert manager.start_app(BlockingSession()) is None  # type: ignore[arg-type]
    assert manager.metrics_snapshot()["rejected_startups_total"] == 1
    manager.release()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert manager.pending_startups == 0
    assert manager.active_sessions == 0


def test_rejected_non_pty_session_releases_limit(mock_channel):
    manager = SessionManager()
    assert manager.try_acquire(1)
    session = TextishSSHServerSession("pkg.mod:App", manager)
    session._channel = mock_channel

    session.session_started()

    assert manager.active_sessions == 0


def test_terminal_dimensions_are_clamped(mock_channel):
    session = TextishSSHServerSession(
        "pkg.mod:App",
        SessionManager(),
        max_terminal_width=120,
        max_terminal_height=40,
    )
    session._channel = mock_channel
    app_session = MagicMock()
    session._app_session = app_session

    session.pty_requested("xterm", (1000, 0, 0, 0), {})
    session.terminal_size_changed(500, 200, 0, 0)

    assert (session._cols, session._rows) == (120, 40)
    app_session.resize.assert_called_once_with(120, 40)


def test_output_buffer_limit_is_applied(mock_channel):
    session = TextishSSHServerSession(
        "pkg.mod:App", SessionManager(), output_buffer_limit=8192
    )

    session.connection_made(mock_channel)

    mock_channel.set_write_buffer_limits.assert_called_once_with(high=8192)


@pytest.mark.asyncio
async def test_input_and_output_metrics_include_render_latency(mock_channel):
    session = TextishSSHServerSession("pkg.mod:App", SessionManager())
    session.connection_made(mock_channel)

    session.data_received(b"hello", None)
    await asyncio.sleep(0)
    session._output_written(12)

    snapshot = session._session_manager.metrics_snapshot()
    assert snapshot["input_bytes_total"] == 5
    assert snapshot["output_bytes_total"] == 12
    assert snapshot["input_renders_total"] == 1
    assert snapshot["input_render_latency_p95_ms"] >= 0


@pytest.mark.asyncio
async def test_idle_timeout_closes_channel(mock_channel):
    session = TextishSSHServerSession(
        "pkg.mod:App", SessionManager(), idle_timeout=0.01
    )
    session.connection_made(mock_channel)

    await asyncio.sleep(0.02)

    mock_channel.close.assert_called_once()
    assert session._session_manager.metrics_snapshot()["idle_disconnects_total"] == 1


def test_pause_writing_closes_slow_client(mock_channel):
    session = TextishSSHServerSession("pkg.mod:App", SessionManager())
    session.connection_made(mock_channel)

    session.pause_writing()

    mock_channel.close.assert_called_once()
    assert (
        session._session_manager.metrics_snapshot()["slow_reader_disconnects_total"]
        == 1
    )


def test_eof_closes_immediately_when_no_app_is_running():
    session = TextishSSHServerSession("pkg.mod:App", SessionManager())

    assert session.eof_received() is False


@pytest.mark.asyncio
async def test_eof_keeps_output_open_for_terminal_cleanup():
    session = TextishSSHServerSession("pkg.mod:App", SessionManager())
    session._run_task = asyncio.create_task(asyncio.sleep(60))

    assert session.eof_received() is True
    await asyncio.sleep(0)

    assert session._run_task.cancelled()

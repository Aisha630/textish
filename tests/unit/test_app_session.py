import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import textish.app_session as app_session_module
from textish.app_session import AppSession


@pytest.mark.asyncio
async def test_send_input_writes_raw_bytes_to_pty(mock_channel):
    session = AppSession("cmd", mock_channel)
    session._master_fd = 123

    with patch("textish.app_session.os.write", return_value=3) as write:
        await session.send_input(b"key")

    write.assert_called_once()
    assert write.call_args.args[0] == 123
    assert bytes(write.call_args.args[1]) == b"key"


@pytest.mark.asyncio
async def test_send_input_retries_partial_writes(mock_channel):
    session = AppSession("cmd", mock_channel)
    session._master_fd = 123

    with patch("textish.app_session.os.write", side_effect=[1, 2]) as write:
        await session.send_input(b"key")

    assert write.call_count == 2
    assert bytes(write.call_args_list[0].args[1]) == b"key"
    assert bytes(write.call_args_list[1].args[1]) == b"ey"


@pytest.mark.asyncio
async def test_send_input_handles_os_error(mock_channel):
    session = AppSession("cmd", mock_channel)
    session._master_fd = 123

    with patch("textish.app_session.os.write", side_effect=OSError):
        await session.send_input(b"key")  # must not raise


@pytest.mark.asyncio
async def test_send_input_waits_when_pty_write_would_block(mock_channel):
    session = AppSession("cmd", mock_channel)
    session._master_fd = 123

    writes = [BlockingIOError, 3]

    def fake_write(*args):
        result = writes.pop(0)
        if isinstance(result, type) and issubclass(result, Exception):
            raise result
        return result

    async def fake_wait(fd):
        assert fd == 123

    with (
        patch("textish.app_session.os.write", side_effect=fake_write),
        patch.object(session, "_wait_for_pty_writable", side_effect=fake_wait),
    ):
        await session.send_input(b"key")


@pytest.mark.asyncio
async def test_read_pty_waits_when_read_would_block(mock_channel):
    session = AppSession("cmd", mock_channel)
    session._master_fd = 123
    reads = [BlockingIOError, b"screen"]

    def fake_read(*args):
        result = reads.pop(0)
        if isinstance(result, type) and issubclass(result, Exception):
            raise result
        return result

    async def fake_wait(fd):
        assert fd == 123

    with (
        patch("textish.app_session.os.read", side_effect=fake_read),
        patch.object(session, "_wait_for_pty_readable", side_effect=fake_wait),
    ):
        data = await session._read_pty()

    assert data == b"screen"


@pytest.mark.asyncio
async def test_resize_updates_pty_window_size(mock_session):
    session = mock_session
    session._master_fd = 123

    with patch("textish.app_session.fcntl.ioctl") as ioctl:
        await session.resize(120, 40)

    assert session._cols == 120
    assert session._rows == 40
    ioctl.assert_called_once()


@pytest.mark.asyncio
async def test_close_terminates_and_waits(mock_session):
    session = mock_session

    await session.close()

    session._process.terminate.assert_called_once()
    session._process.wait.assert_awaited()


@pytest.mark.asyncio
async def test_close_kills_process_on_timeout(mock_session):
    session = mock_session

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    with patch("textish.app_session.asyncio.wait_for", side_effect=fake_wait_for):
        await session.close()

    session._process.kill.assert_called_once()
    session._process.wait.assert_awaited()


@pytest.mark.asyncio
async def test_run_forwards_pty_output_to_channel(mock_channel, monkeypatch):
    session = AppSession("cmd", mock_channel)
    proc = MagicMock()
    proc.returncode = 0
    proc.wait = AsyncMock()

    async def fake_create_subprocess_shell(*args, **kwargs):
        slave_fd = os.dup(kwargs["stdout"])
        loop = app_session_module.asyncio.get_running_loop()
        loop.call_soon(os.write, slave_fd, b"hello screen\n")
        loop.call_later(0.01, os.close, slave_fd)
        return proc

    monkeypatch.setattr(
        app_session_module.asyncio,
        "create_subprocess_shell",
        fake_create_subprocess_shell,
    )

    await session.run()

    assert mock_channel.write.call_args is not None
    assert b"hello screen" in mock_channel.write.call_args.args[0]
    mock_channel.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_waits_for_subprocess_after_natural_pty_eof(
    mock_channel, monkeypatch
):
    session = AppSession("cmd", mock_channel)
    proc = MagicMock()
    proc.returncode = None
    proc.wait = AsyncMock()

    async def fake_create_subprocess_shell(*args, **kwargs):
        return proc

    monkeypatch.setattr(
        app_session_module.asyncio,
        "create_subprocess_shell",
        fake_create_subprocess_shell,
    )

    await session.run()

    proc.wait.assert_awaited()
    proc.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_run_terminates_subprocess_if_natural_exit_wait_times_out(
    mock_channel, monkeypatch
):
    session = AppSession("cmd", mock_channel)
    proc = MagicMock()
    proc.returncode = None
    proc.wait = AsyncMock()

    async def fake_create_subprocess_shell(*args, **kwargs):
        return proc

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(
        app_session_module.asyncio,
        "create_subprocess_shell",
        fake_create_subprocess_shell,
    )

    with patch("textish.app_session.asyncio.wait_for", side_effect=fake_wait_for):
        await session.run()

    proc.terminate.assert_called_once()


def test_build_subprocess_env_uses_explicit_env_only(mock_channel, monkeypatch):
    monkeypatch.setenv("TEXTISH_PARENT", "parent")
    session = AppSession("cmd", mock_channel, env={"APP_ENV": "configured"})

    env = session._build_subprocess_env()

    assert "TEXTISH_PARENT" not in env
    assert env["APP_ENV"] == "configured"
    assert env["COLUMNS"] == "80"
    assert env["ROWS"] == "24"
    assert env["TERM"] == "xterm-256color"


def test_terminal_env_overrides_configured_env(mock_channel, monkeypatch):
    monkeypatch.setenv("TEXTISH_PARENT", "parent")
    session = AppSession(
        "cmd",
        mock_channel,
        cols=100,
        rows=30,
        term_type="screen",
        env={"APP_ENV": "configured", "TERM": "ignored"},
    )

    env = session._build_subprocess_env()

    assert "TEXTISH_PARENT" not in env
    assert env == {
        "APP_ENV": "configured",
        "COLUMNS": "100",
        "ROWS": "30",
        "TERM": "screen",
    }

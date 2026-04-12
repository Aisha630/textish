import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from textish.app_session import AppSession
from textish.protocol import encode_packet


@pytest.mark.asyncio
async def test_send_input_writes_encoded_packet(mock_channel, mock_stdin):
    session = AppSession("cmd", mock_channel)
    mock_process = MagicMock()
    mock_process.stdin = mock_stdin
    session._process = mock_process

    await session.send_input(b"key")

    mock_stdin.write.assert_called_once_with(encode_packet(b"D", b"key"))
    mock_stdin.drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_input_handles_broken_pipe(mock_channel):
    session = AppSession("cmd", mock_channel)
    stdin = MagicMock()
    stdin.write.side_effect = BrokenPipeError
    mock_process = MagicMock()
    mock_process.stdin = stdin
    session._process = mock_process

    await session.send_input(b"key")  # must not raise


@pytest.mark.asyncio
async def test_send_input_handles_connection_reset(mock_channel):
    session = AppSession("cmd", mock_channel)
    stdin = MagicMock()
    stdin.drain = AsyncMock(side_effect=ConnectionResetError)
    mock_process = MagicMock()
    mock_process.stdin = stdin
    session._process = mock_process

    await session.send_input(b"key")  # must not raise


@pytest.mark.asyncio
async def test_resize_sends_correct_meta_packet(mock_session):
    session = mock_session

    await session.resize(120, 40)

    expected = encode_packet(
        b"M", json.dumps({"type": "resize", "width": 120, "height": 40}).encode()
    )

    session._process.stdin.write.assert_called_once_with(expected)
    assert session._cols == 120
    assert session._rows == 40


@pytest.mark.asyncio
async def test_close_sends_quit_and_waits(mock_session):
    session = mock_session

    await session.close()

    expected = encode_packet(b"M", json.dumps({"type": "quit"}).encode())
    session._process.stdin.write.assert_called_once_with(expected)
    session._process.stdin.close.assert_called_once()
    session._process.wait.assert_awaited()


@pytest.mark.asyncio
async def test_close_kills_process_on_timeout(mock_session):
    session = mock_session

    with patch(
        "textish.app_session.asyncio.wait_for", side_effect=asyncio.TimeoutError
    ):
        await session.close()

    session._process.kill.assert_called_once()
    session._process.wait.assert_awaited()


@pytest.mark.asyncio
async def test_run_handshake_failure_closes_channel(mock_channel, mock_process):
    session = AppSession("cmd", mock_channel)
    mock_process(b"not ganglion\n", stderr_data=b"some error")

    await session.run()

    mock_channel.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_forwards_display_packet_to_channel(mock_channel, mock_process):
    session = AppSession("cmd", mock_channel)
    mock_process(b"__GANGLION__\n" + encode_packet(b"D", b"hello screen"))

    await session.run()

    mock_channel.write.assert_called_once_with(b"hello screen")
    mock_channel.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_handles_exit_meta_packet(mock_channel, mock_process):
    session = AppSession("cmd", mock_channel)
    exit_payload = json.dumps({"type": "exit"}).encode()
    proc = mock_process(
        b"__GANGLION__\n" + encode_packet(b"M", exit_payload), returncode=0
    )

    await session.run()

    proc.wait.assert_awaited()
    mock_channel.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_terminates_subprocess_still_running_on_exit(
    mock_channel, mock_process
):
    session = AppSession("cmd", mock_channel)
    mock_process(b"__GANGLION__\n", returncode=None)

    await session.run()

    session._process.terminate.assert_called_once()
    session._process.wait.assert_awaited()

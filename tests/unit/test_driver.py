"""Unit tests for the shared-interpreter SSH driver."""

from unittest.mock import MagicMock

import pytest

from textish.inprocess.driver import bind_ssh_driver


def _make_driver(channel: MagicMock | None = None):
    channel = channel or MagicMock()
    on_ready = MagicMock()
    driver_class = bind_ssh_driver(channel, on_ready)
    return channel, on_ready, driver_class(MagicMock(), size=(80, 24))


@pytest.mark.asyncio
async def test_write_encodes_text_as_utf8():
    channel, _, driver = _make_driver()

    driver.write("café")

    channel.write.assert_called_once_with("café".encode())


@pytest.mark.asyncio
async def test_start_application_mode_initializes_terminal_and_reports_ready():
    channel, on_ready, driver = _make_driver()
    driver.send_message = MagicMock()

    driver.start_application_mode()

    output = channel.write.call_args.args[0]
    assert output.startswith(b"\x1b[?1049h")
    assert driver._alive is True
    on_ready.assert_called_once_with(driver)

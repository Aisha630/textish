"""Unit tests for the subinterpreter worker's QueueDriver output path.

These exercise the driver in the main interpreter (no subinterpreter needed), so
they run on any Python version. They cover the output protocol that the
main-interpreter pump depends on.
"""

from unittest.mock import MagicMock

import pytest

from textish.subinterp._worker import _bind_queue_driver


@pytest.mark.asyncio
async def test_write_emits_data_message():
    messages = []
    bound = _bind_queue_driver(messages.append, {})
    driver = bound(MagicMock(), size=(80, 24))

    driver.write("hello")

    assert messages == [("D", b"hello")]


@pytest.mark.asyncio
async def test_write_encodes_utf8():
    messages = []
    bound = _bind_queue_driver(messages.append, {})
    driver = bound(MagicMock(), size=(80, 24))

    driver.write("café")

    assert messages == [("D", "café".encode())]


@pytest.mark.asyncio
async def test_bind_registers_live_instance_in_holder():
    holder = {}
    bound = _bind_queue_driver(lambda _m: None, holder)
    driver = bound(MagicMock(), size=(80, 24))

    assert holder["driver"] is driver


@pytest.mark.asyncio
async def test_start_application_mode_enters_alt_screen_first():
    messages = []
    bound = _bind_queue_driver(messages.append, {})
    driver = bound(MagicMock(), size=(80, 24))

    # _post schedules an app message; with a mock app we only care about the
    # byte output, so stub it out.
    driver._post = lambda _msg: None
    driver.start_application_mode()

    assert messages[0] == ("D", b"\x1b[?1049h")  # alternate screen enter
    assert driver._alive is True

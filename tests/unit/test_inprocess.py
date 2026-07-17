import asyncio
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest
from textual.app import App

from textish.inprocess.driver import SSHDriver
from textish.inprocess.session import InProcessAppSession


class _ExitApp(App[Any]):
    def on_mount(self) -> None:
        self.exit()


class _SystemExitApp(App[Any]):
    def on_mount(self) -> None:
        raise SystemExit(2)


@pytest.mark.asyncio
async def test_concurrent_apps_are_distinct_and_preserve_process_streams():
    created = []

    def factory():
        app = _ExitApp()
        created.append(app)
        return app

    original_stdout = sys.stdout
    sessions = [InProcessAppSession(factory, MagicMock()) for _ in range(5)]

    await asyncio.gather(*(session.run() for session in sessions))

    assert len({id(app) for app in created}) == 5
    assert sys.stdout is original_stdout


def test_input_received_during_startup_is_drained_when_driver_starts():
    session = InProcessAppSession(lambda: _ExitApp(), MagicMock())
    driver = MagicMock(spec=SSHDriver)

    session.send_input(b"early")
    session._driver_ready(driver)

    driver.feed.assert_called_once_with(b"early")


def test_early_input_is_bounded_and_keeps_the_available_prefix():
    session = InProcessAppSession(lambda: _ExitApp(), MagicMock())
    driver = MagicMock(spec=SSHDriver)

    session.send_input(b"a" * (64 * 1024 - 2))
    session.send_input(b"bcde")
    session._driver_ready(driver)

    driver.feed.assert_called_once_with(b"a" * (64 * 1024 - 2) + b"bc")


@pytest.mark.asyncio
async def test_system_exit_in_one_app_does_not_escape_session():
    channel = MagicMock()
    session = InProcessAppSession(_SystemExitApp, channel)

    await session.run()

    channel.close.assert_called_once()
    channel.write.assert_any_call(b"\r\ntextish app failed; check the server logs.\r\n")

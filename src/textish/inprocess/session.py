"""Lifecycle for one Textual app instance in the shared interpreter."""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Callable
from contextlib import nullcontext
from functools import cache
from typing import Any, cast

import asyncssh
import textual.app as textual_app
from textual.app import App

from .driver import SSHDriver, bind_ssh_driver

log = logging.getLogger("textish")

_MAX_EARLY_INPUT = 64 * 1024


# Textual normally captures print output by replacing process-global streams for
# an app's entire lifetime. Concurrent apps would restore those globals out of
# order, so server mode keeps stdout/stderr attached to the server process.
textual_app.redirect_stdout = nullcontext  # type: ignore[attr-defined,assignment]
textual_app.redirect_stderr = nullcontext  # type: ignore[attr-defined,assignment]


AppSource = str | Callable[[], object]


@cache
def _load_import_factory(app_ref: str) -> Callable[[], object]:
    """Resolve an import reference once for all sessions using it."""
    module_name, _, attr = app_ref.partition(":")
    factory: Any = importlib.import_module(module_name)
    for part in attr.split("."):
        factory = getattr(factory, part)
    if not callable(factory):
        raise TypeError(f"{app_ref} is not callable")
    return cast("Callable[[], object]", factory)


def _load_factory(app_source: AppSource) -> Callable[[], object]:
    if isinstance(app_source, str):
        return _load_import_factory(app_source)
    return app_source


class InProcessAppSession:
    """Run one independent app instance on the server's asyncio loop."""

    __slots__ = (
        "_app_source",
        "_channel",
        "_cols",
        "_rows",
        "_app",
        "_driver",
        "_early_input",
    )

    def __init__(
        self,
        app_source: AppSource,
        channel: asyncssh.SSHServerChannel[bytes],
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        self._app_source = app_source
        self._channel = channel
        self._cols = cols
        self._rows = rows
        self._app: App[Any] | None = None
        self._driver: SSHDriver | None = None
        self._early_input = bytearray()

    async def run(self, ready_callback: Callable[[], None] | None = None) -> None:
        """Construct and run a fresh app instance until exit or cancellation."""
        ready_sent = False

        def mark_ready() -> None:
            nonlocal ready_sent
            if not ready_sent and ready_callback is not None:
                ready_sent = True
                ready_callback()

        async def app_ready(_pilot: object) -> None:
            mark_ready()

        try:
            app = _load_factory(self._app_source)()
            if not isinstance(app, App):
                raise TypeError(f"{self._app_source!r} did not create a Textual App")
            self._app = app
            app.driver_class = bind_ssh_driver(self._channel, self._driver_ready)
            await app.run_async(
                size=(self._cols, self._rows),
                auto_pilot=app_ready,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            log.error("App session failed: %s", exc, exc_info=True)
            try:
                self._channel.write(
                    b"\r\ntextish app failed; check the server logs.\r\n"
                )
            except OSError:
                pass
        finally:
            mark_ready()
            self._channel.close()

    def _driver_ready(self, driver: SSHDriver) -> None:
        self._driver = driver
        driver.resize(self._cols, self._rows)
        if self._early_input:
            driver.feed(bytes(self._early_input))
            self._early_input.clear()

    def send_input(self, data: bytes) -> None:
        """Deliver terminal input, buffering a small amount during app startup."""
        if self._driver is not None:
            self._driver.feed(data)
            return
        available = _MAX_EARLY_INPUT - len(self._early_input)
        if available > 0:
            self._early_input.extend(data[:available])

    def resize(self, cols: int, rows: int) -> None:
        """Resize this app instance without affecting any other session."""
        self._cols = cols
        self._rows = rows
        if self._driver is not None:
            self._driver.resize(cols, rows)

    def close(self) -> None:
        """Ask this session's app instance to exit cleanly."""
        if self._app is not None:
            self._app.exit()

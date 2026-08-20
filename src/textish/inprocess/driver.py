"""Textual driver which treats an AsyncSSH channel as its terminal."""

from __future__ import annotations

from codecs import getincrementaldecoder
from collections.abc import Callable
from functools import partial
from typing import Any, Protocol, cast

from textual import events
from textual._xterm_parser import XTermParser
from textual.driver import Driver
from textual.geometry import Size

_START_APPLICATION_MODE = (
    "\x1b[?1049h"  # alternate screen
    "\x1b[?1000h\x1b[?1003h\x1b[?1015h\x1b[?1006h"  # mouse tracking
    "\x1b[?25l"  # hide cursor
    "\x1b[?2004h"  # bracketed paste
)
_STOP_APPLICATION_MODE = (
    "\x1b[?2004l\x1b[?1000l\x1b[?1003l\x1b[?1015l\x1b[?1006l\x1b[?25h\x1b[?1049l"
)


class ByteChannel(Protocol):
    """Minimal channel surface needed by ``SSHDriver``."""

    def write(self, data: bytes) -> None: ...


class SSHDriver(Driver):
    """A Textual driver bound directly to one SSH session channel."""

    _channel: ByteChannel

    def __init__(
        self,
        app: Any,
        *,
        channel: ByteChannel,
        on_ready: Callable[[SSHDriver], None],
        on_output: Callable[[int], None] | None = None,
        debug: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(app, debug=debug, mouse=mouse, size=size)
        self._channel = channel
        self._on_ready = on_ready
        self._on_output = on_output
        self._parser = XTermParser(debug=debug)
        self._decoder = getincrementaldecoder("utf-8")()
        self._alive = False

    def write(self, data: str) -> None:
        encoded = data.encode("utf-8", errors="replace")
        try:
            self._channel.write(encoded)
            if self._on_output is not None:
                self._on_output(len(encoded))
        except OSError:
            pass

    def flush(self) -> None:
        """AsyncSSH buffers channel writes itself."""

    def feed(self, data: bytes) -> None:
        """Decode and deliver raw terminal input to Textual."""
        if not self._alive:
            return
        text = self._decoder.decode(data)
        if not text:
            return
        for event in self._parser.feed(text):
            self.process_message(event)

    def start_application_mode(self) -> None:
        self._alive = True
        self.write(_START_APPLICATION_MODE)
        size = Size(80, 24) if self._size is None else Size(*self._size)
        self.send_message(events.Resize(size, size))
        self._app.call_later(self._app.post_message, events.AppFocus())
        self._on_ready(self)

    def disable_input(self) -> None:
        self._alive = False

    def stop_application_mode(self) -> None:
        self._alive = False
        self.write(_STOP_APPLICATION_MODE)

    def close(self) -> None:
        self._alive = False

    def resize(self, width: int, height: int) -> None:
        self._size = (width, height)
        size = Size(width, height)
        self.send_message(events.Resize(size, size))


def bind_ssh_driver(
    channel: ByteChannel,
    on_ready: Callable[[SSHDriver], None],
    on_output: Callable[[int], None] | None = None,
) -> type[SSHDriver]:
    """Bind a channel to the driver constructor expected by Textual."""
    factory = partial(
        SSHDriver,
        channel=channel,
        on_ready=on_ready,
        on_output=on_output,
    )
    # Textual annotates driver_class as a class, but only calls it as a factory.
    return cast("type[SSHDriver]", factory)

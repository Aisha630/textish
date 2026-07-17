"""Tiny reference/test/benchmark apps for the subinterpreter backend.

Kept inside the package so they are importable by dotted path from any
subinterpreter (e.g. ``textish.subinterp._demo_app:EchoApp``) without depending
on ``sys.path`` tweaks. Used by the example, the end-to-end test, and the
benchmark script.
"""

from __future__ import annotations

import os

from textual.app import App, ComposeResult
from textual.widgets import Input, Static


class EchoApp(App):
    """Shows a banner and echoes typed input, for verifying the bridge."""

    def compose(self) -> ComposeResult:
        yield Static("SUBINTERP-BANNER", id="banner")
        yield Static("", id="echo")
        yield Input(id="in")

    def on_mount(self) -> None:
        self.query_one("#in", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#echo", Static).update(f"echo:{event.value}")


class BenchApp(App):
    """Benchmark app: burns a fixed amount of CPU on mount, then renders.

    The work size is read from the ``TEXTISH_BENCH_WORK`` environment variable
    (a loop-iteration count, default 0). CPU work is what exposes the difference
    between GIL-serialised backends (in-process) and truly parallel ones
    (subinterpreters), so the benchmark can set it to a non-zero value.
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="banner")

    def on_mount(self) -> None:
        iterations = int(os.environ.get("TEXTISH_BENCH_WORK", "0"))
        total = 0.0
        for i in range(iterations):
            total += (i * i) ** 0.5
        # Render the banner only after the work is done, so "banner seen" on the
        # client side marks the end of this session's CPU work.
        self.query_one("#banner", Static).update(f"BENCH-READY {total:.0f}")

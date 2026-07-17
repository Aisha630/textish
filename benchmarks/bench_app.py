"""Small Textual app used by the shared-session and real-SSH benchmarks."""

import os
from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import Static


class BenchApp(App[Any]):
    """Burn optional CPU work before rendering the benchmark marker."""

    def compose(self) -> ComposeResult:
        yield Static("", id="banner")

    def on_mount(self) -> None:
        iterations = int(os.environ.get("TEXTISH_BENCH_WORK", "0"))
        total = 0.0
        for i in range(iterations):
            total += (i * i) ** 0.5
        self.query_one("#banner", Static).update(f"BENCH-READY {total:.0f}")

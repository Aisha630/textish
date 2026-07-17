"""Benchmark the textish subinterpreter backend: memory and render time.

Runs many sessions, each in its own subinterpreter, and reports per-session
memory and the wall time to render them all. Requires Python 3.14+.

Usage::

    poetry run python benchmarks/bench_subinterp.py                 # 50 sessions
    poetry run python benchmarks/bench_subinterp.py --sessions 100
    poetry run python benchmarks/bench_subinterp.py --work 200000   # add CPU work

``--work N`` makes each app burn N loop iterations on mount before it renders.
CPU work is what exposes true parallelism: with its own GIL per session, the
subinterpreter backend should keep scaling across cores as sessions get busier.

Memory is peak RSS growth over a baseline taken after imports, divided by the
session count, so it is approximate but indicative.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import resource
import sys
import time

# Allow running straight from a checkout without installing.
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if os.path.isdir(_SRC):
    sys.path.insert(0, os.path.abspath(_SRC))

BANNER = b"BENCH-READY"
APP_REF = "textish.subinterp._demo_app:BenchApp"


def _rss_mb() -> float:
    """Peak RSS in MB (ru_maxrss is KB on Linux, bytes on macOS)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


class _FakeChannel:
    """Stand-in for an asyncssh channel that timestamps the first render."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.first_render: float | None = None
        self.closed = False

    def write(self, data: bytes) -> None:
        self._buf.extend(data)
        if self.first_render is None and BANNER in self._buf:
            self.first_render = time.perf_counter()

    def close(self) -> None:
        self.closed = True


async def _await_all_rendered(channels: list[_FakeChannel], timeout: float) -> None:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if all(c.first_render is not None for c in channels):
            return
        await asyncio.sleep(0.01)


async def _bench(n: int) -> dict:
    from textish.subinterp import SubinterpAppSession

    baseline = _rss_mb()
    channels = [_FakeChannel() for _ in range(n)]
    sessions = [SubinterpAppSession(APP_REF, c, 80, 24) for c in channels]
    t0 = time.perf_counter()
    tasks = [asyncio.create_task(s.run()) for s in sessions]
    await _await_all_rendered(channels, timeout=120)
    peak = _rss_mb()
    for s in sessions:
        await s.close()
    await asyncio.gather(
        *(asyncio.wait_for(t, 30) for t in tasks), return_exceptions=True
    )
    rendered = [c.first_render for c in channels if c.first_render is not None]
    return {
        "sessions": n,
        "rendered": len(rendered),
        "first_ms": (min(rendered) - t0) * 1000 if rendered else None,
        "all_ms": (max(rendered) - t0) * 1000 if rendered else None,
        "mem_per_session_kb": (peak - baseline) * 1024 / n if n else 0,
        "mem_total_mb": peak - baseline,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=50)
    parser.add_argument("--work", type=int, default=0, help="CPU loop iters per app")
    args = parser.parse_args()

    from textish.subinterp import SUBINTERP_AVAILABLE

    if not SUBINTERP_AVAILABLE:
        raise SystemExit("Requires Python 3.14+ (concurrent.interpreters).")

    os.environ["TEXTISH_BENCH_WORK"] = str(args.work)
    print(
        f"Subinterpreter backend: {args.sessions} sessions, work={args.work} "
        f"(Python {sys.version.split()[0]})"
    )
    r = asyncio.run(_bench(args.sessions))
    first = f"{r['first_ms']:.0f} ms" if r["first_ms"] is not None else "-"
    allms = f"{r['all_ms']:.0f} ms" if r["all_ms"] is not None else "-"
    print(f"  rendered:            {r['rendered']}/{r['sessions']}")
    print(f"  time to first render: {first}")
    print(f"  time to render all:   {allms}")
    print(f"  memory per session:   {r['mem_per_session_kb']:.0f} KB")
    print(f"  memory total:         {r['mem_total_mb']:.1f} MB")


if __name__ == "__main__":
    main()

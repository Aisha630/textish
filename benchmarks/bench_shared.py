"""Benchmark shared-interpreter session memory, startup, and event-loop lag.

Usage::

    poetry run python benchmarks/bench_shared.py --sessions 100
    poetry run python benchmarks/bench_shared.py --sessions 1000
    poetry run python benchmarks/bench_shared.py --sessions 100 --work 200000

``--work`` intentionally blocks each app's mount handler. It demonstrates why
served apps must move blocking or CPU-heavy work off the shared event loop.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import resource
import sys
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
_BENCHMARKS = os.path.dirname(__file__)
for path in (_BENCHMARKS, _SRC):
    if path not in sys.path:
        sys.path.insert(0, path)

BANNER = b"BENCH-READY"
APP_REF = "bench_app:BenchApp"


def _rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


class _FakeChannel:
    def __init__(self) -> None:
        self._buf = bytearray()
        self.first_render: float | None = None
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.first_render is not None:
            return
        self._buf.extend(data)
        if BANNER in self._buf:
            self.first_render = time.perf_counter()
            # Model a client which consumes output instead of retaining a copy.
            self._buf.clear()

    def close(self) -> None:
        self.closed = True


async def _await_all_rendered(channels: list[_FakeChannel], timeout: float) -> None:
    async with asyncio.timeout(timeout):
        while not all(channel.first_render is not None for channel in channels):
            await asyncio.sleep(0.005)


async def _measure_loop_lag(stop: asyncio.Event, samples: list[float]) -> None:
    loop = asyncio.get_running_loop()
    interval = 0.01
    while not stop.is_set():
        target = loop.time() + interval
        await asyncio.sleep(interval)
        samples.append(max(0, loop.time() - target))


async def _bench(count: int) -> dict[str, float | int | None]:
    from textish.inprocess import InProcessAppSession
    from textish.server import SessionManager

    baseline = _rss_mb()
    channels = [_FakeChannel() for _ in range(count)]
    sessions = [InProcessAppSession(APP_REF, channel) for channel in channels]
    manager = SessionManager()
    lag_samples: list[float] = []
    stop_lag = asyncio.Event()
    lag_task = asyncio.create_task(_measure_loop_lag(stop_lag, lag_samples))
    started = time.perf_counter()
    tasks = [asyncio.create_task(manager.run_app(session)) for session in sessions]
    try:
        await _await_all_rendered(channels, timeout=120)
        peak = _rss_mb()
    finally:
        for session in sessions:
            session.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        stop_lag.set()
        await lag_task

    rendered = [
        channel.first_render for channel in channels if channel.first_render is not None
    ]
    return {
        "sessions": count,
        "rendered": len(rendered),
        "first_ms": (min(rendered) - started) * 1000 if rendered else None,
        "all_ms": (max(rendered) - started) * 1000 if rendered else None,
        "max_loop_lag_ms": max(lag_samples, default=0) * 1000,
        "mem_per_session_kb": (peak - baseline) * 1024 / count if count else 0,
        "mem_total_mb": peak - baseline,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=100)
    parser.add_argument("--work", type=int, default=0, help="CPU loop iters per app")
    args = parser.parse_args()
    if args.sessions < 1:
        parser.error("--sessions must be at least 1")

    os.environ["TEXTISH_BENCH_WORK"] = str(args.work)
    print(
        f"Shared interpreter: {args.sessions} sessions, work={args.work} "
        f"(Python {sys.version.split()[0]})"
    )
    result = asyncio.run(_bench(args.sessions))
    first = f"{result['first_ms']:.0f} ms" if result["first_ms"] is not None else "-"
    all_rendered = f"{result['all_ms']:.0f} ms" if result["all_ms"] is not None else "-"
    print(f"  rendered:             {result['rendered']}/{result['sessions']}")
    print(f"  time to first render: {first}")
    print(f"  time to render all:   {all_rendered}")
    print(f"  max event-loop lag:   {result['max_loop_lag_ms']:.1f} ms")
    print(f"  memory per session:   {result['mem_per_session_kb']:.0f} KB")
    print(f"  memory total:         {result['mem_total_mb']:.1f} MB")


if __name__ == "__main__":
    main()

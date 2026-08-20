"""Dependency-free runtime metrics for one textish worker process."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable

type MetricValue = int | float | str
type MetricsSnapshot = dict[str, MetricValue]
type MetricsCallback = Callable[[MetricsSnapshot], None | Awaitable[None]]

log = logging.getLogger("textish.metrics")


def _percentile(samples: deque[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


class ServerMetrics:
    """Counters and bounded latency samples owned by one event loop."""

    __slots__ = (
        "_started_at",
        "_rejected_ssh_connections",
        "_rejected_auth",
        "_rejected_sessions",
        "_rejected_startups",
        "_idle_disconnects",
        "_slow_reader_disconnects",
        "_app_failures",
        "_input_bytes",
        "_output_bytes",
        "_startups_ready",
        "_startups_not_ready",
        "_input_renders",
        "_startup_seconds",
        "_input_render_seconds",
        "_loop_lag_seconds",
    )

    def __init__(self, sample_limit: int = 4096) -> None:
        self._started_at = time.monotonic()
        self._rejected_ssh_connections = 0
        self._rejected_auth = 0
        self._rejected_sessions = 0
        self._rejected_startups = 0
        self._idle_disconnects = 0
        self._slow_reader_disconnects = 0
        self._app_failures = 0
        self._input_bytes = 0
        self._output_bytes = 0
        self._startups_ready = 0
        self._startups_not_ready = 0
        self._input_renders = 0
        self._startup_seconds: deque[float] = deque(maxlen=sample_limit)
        self._input_render_seconds: deque[float] = deque(maxlen=sample_limit)
        self._loop_lag_seconds: deque[float] = deque(maxlen=sample_limit)

    def reject_ssh_connection(self) -> None:
        self._rejected_ssh_connections += 1

    def reject_auth(self) -> None:
        self._rejected_auth += 1

    def reject_session(self) -> None:
        self._rejected_sessions += 1

    def reject_startup(self) -> None:
        self._rejected_startups += 1

    def disconnect_idle(self) -> None:
        self._idle_disconnects += 1

    def disconnect_slow_reader(self) -> None:
        self._slow_reader_disconnects += 1

    def app_failed(self) -> None:
        self._app_failures += 1

    def add_input_bytes(self, count: int) -> None:
        self._input_bytes += count

    def add_output_bytes(self, count: int) -> None:
        self._output_bytes += count

    def startup_finished(self, seconds: float, *, ready: bool) -> None:
        if ready:
            self._startups_ready += 1
            self._startup_seconds.append(seconds)
        else:
            self._startups_not_ready += 1

    def observe_input_render(self, seconds: float) -> None:
        self._input_renders += 1
        self._input_render_seconds.append(seconds)

    def observe_loop_lag(self, seconds: float) -> None:
        self._loop_lag_seconds.append(seconds)

    def snapshot(
        self,
        *,
        ssh_connections: int,
        authenticating: int,
        active_sessions: int,
        pending_startups: int,
        app_tasks: int,
    ) -> MetricsSnapshot:
        """Return a JSON-serializable point-in-time worker snapshot."""
        return {
            "kind": "textish_metrics",
            "timestamp": time.time(),
            "pid": os.getpid(),
            "uptime_seconds": time.monotonic() - self._started_at,
            "ssh_connections": ssh_connections,
            "authenticating": authenticating,
            "active_sessions": active_sessions,
            "pending_startups": pending_startups,
            "app_tasks": app_tasks,
            "rejected_ssh_connections_total": self._rejected_ssh_connections,
            "rejected_auth_total": self._rejected_auth,
            "rejected_sessions_total": self._rejected_sessions,
            "rejected_startups_total": self._rejected_startups,
            "idle_disconnects_total": self._idle_disconnects,
            "slow_reader_disconnects_total": self._slow_reader_disconnects,
            "app_failures_total": self._app_failures,
            "input_bytes_total": self._input_bytes,
            "output_bytes_total": self._output_bytes,
            "startups_ready_total": self._startups_ready,
            "startups_not_ready_total": self._startups_not_ready,
            "input_renders_total": self._input_renders,
            "startup_latency_p50_ms": _percentile(self._startup_seconds, 0.50) * 1000,
            "startup_latency_p95_ms": _percentile(self._startup_seconds, 0.95) * 1000,
            "input_render_latency_p50_ms": _percentile(self._input_render_seconds, 0.50)
            * 1000,
            "input_render_latency_p95_ms": _percentile(self._input_render_seconds, 0.95)
            * 1000,
            "event_loop_lag_p95_ms": _percentile(self._loop_lag_seconds, 0.95) * 1000,
        }


async def run_metrics_reporter(
    snapshot: Callable[[], MetricsSnapshot],
    metrics: ServerMetrics,
    interval: float,
    callback: MetricsCallback | None,
) -> None:
    """Periodically observe event-loop lag and publish worker snapshots."""
    loop = asyncio.get_running_loop()
    expected = loop.time() + interval
    while True:
        await asyncio.sleep(interval)
        now = loop.time()
        metrics.observe_loop_lag(max(0.0, now - expected))
        expected = now + interval
        point = snapshot()
        try:
            if callback is None:
                log.info(json.dumps(point, sort_keys=True, separators=(",", ":")))
            else:
                result = callback(point)
                if inspect.isawaitable(result):
                    await result
        except Exception:
            log.exception("Metrics callback failed")

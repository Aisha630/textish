import asyncio

import pytest

from textish.metrics import ServerMetrics, run_metrics_reporter


def test_metrics_snapshot_contains_counters_gauges_and_latency_summaries():
    metrics = ServerMetrics()
    metrics.reject_ssh_connection()
    metrics.reject_auth()
    metrics.reject_session()
    metrics.reject_startup()
    metrics.disconnect_idle()
    metrics.disconnect_slow_reader()
    metrics.app_failed()
    metrics.add_input_bytes(12)
    metrics.add_output_bytes(34)
    metrics.startup_finished(0.1, ready=True)
    metrics.startup_finished(0.2, ready=True)
    metrics.startup_finished(0.3, ready=False)
    metrics.observe_input_render(0.04)
    metrics.observe_loop_lag(0.005)

    snapshot = metrics.snapshot(
        ssh_connections=3,
        authenticating=1,
        active_sessions=2,
        pending_startups=1,
        app_tasks=2,
    )

    assert snapshot["kind"] == "textish_metrics"
    assert snapshot["ssh_connections"] == 3
    assert snapshot["active_sessions"] == 2
    assert snapshot["rejected_ssh_connections_total"] == 1
    assert snapshot["rejected_auth_total"] == 1
    assert snapshot["rejected_sessions_total"] == 1
    assert snapshot["rejected_startups_total"] == 1
    assert snapshot["idle_disconnects_total"] == 1
    assert snapshot["slow_reader_disconnects_total"] == 1
    assert snapshot["app_failures_total"] == 1
    assert snapshot["input_bytes_total"] == 12
    assert snapshot["output_bytes_total"] == 34
    assert snapshot["startups_ready_total"] == 2
    assert snapshot["startups_not_ready_total"] == 1
    assert snapshot["input_renders_total"] == 1
    assert snapshot["startup_latency_p50_ms"] == pytest.approx(100)
    assert snapshot["startup_latency_p95_ms"] == pytest.approx(200)
    assert snapshot["input_render_latency_p95_ms"] == pytest.approx(40)
    assert snapshot["event_loop_lag_p95_ms"] == pytest.approx(5)


@pytest.mark.asyncio
async def test_metrics_reporter_calls_async_callback():
    metrics = ServerMetrics()
    received = []
    reported = asyncio.Event()

    async def callback(snapshot):
        received.append(snapshot)
        reported.set()

    task = asyncio.create_task(
        run_metrics_reporter(
            lambda: metrics.snapshot(
                ssh_connections=0,
                authenticating=0,
                active_sessions=0,
                pending_startups=0,
                app_tasks=0,
            ),
            metrics,
            0.001,
            callback,
        )
    )
    await asyncio.wait_for(reported.wait(), 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(received) == 1
    assert received[0]["kind"] == "textish_metrics"

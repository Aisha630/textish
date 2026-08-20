"""Exercise real textish sessions and report startup, interaction, and RSS.

This starts an isolated textish server process, opens actual SSH connections and
PTY channels, waits for Textual to render, and then sends simultaneous input to
every held session. A tiny invocation is exercised by the integration suite;
larger runs are intended for explicit capacity and regression testing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import multiprocessing as mp
import os
import signal
import socket
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from multiprocessing.process import BaseProcess
from pathlib import Path

import asyncssh
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from textish import _run_server
from textish.config import AppConfig


class BenchmarkApp(App[None]):
    """Small deterministic app which makes input-to-render latency measurable."""

    def compose(self) -> ComposeResult:
        yield Static("TEXTISH_READY", id="output")
        yield Input(id="input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#output", Static).update(f"TEXTISH_ECHO:{event.value}")


@dataclass
class HeldSession:
    connection: asyncssh.SSHClientConnection
    process: asyncssh.SSHClientProcess[bytes]
    value: str = ""


def _server_process(
    port: int,
    workers: int,
    max_sessions: int,
    max_startups: int,
    max_pending_startups: int,
    app_ref: str | None,
) -> None:
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="textish-benchmark-") as temp_dir:
        app_config = (
            AppConfig(app_ref=app_ref, import_paths=(os.getcwd(),))
            if app_ref is not None
            else AppConfig(app_factory=BenchmarkApp)
        )
        config = replace(
            app_config,
            host="127.0.0.1",
            port=port,
            host_key_path=os.path.join(temp_dir, "host_key"),
            workers=workers,
            max_connections=max_sessions,
            max_ssh_connections=max_sessions,
            max_startups=max_startups,
            max_pending_startups=max_pending_startups,
            backlog=max(128, max_sessions),
            login_timeout=30,
        )
        try:
            _run_server(config)
        except KeyboardInterrupt:
            pass


def _reserve_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _session_capacity(max_sessions: int, churn_ratio: float) -> int:
    """Allow replacements while closed transports finish releasing their slots."""
    return max_sessions + math.ceil(max_sessions * churn_ratio)


def _wait_for_port(port: int, process: BaseProcess, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.exitcode is not None:
            raise RuntimeError(
                f"benchmark server exited with status {process.exitcode}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError("benchmark server did not start in time")


def _process_rows() -> list[tuple[int, int, int]]:
    output = subprocess.check_output(["ps", "-e", "-o", "pid=,ppid=,rss="], text=True)
    rows: list[tuple[int, int, int]] = []
    for line in output.splitlines():
        pid, parent_pid, rss = map(int, line.split())
        rows.append((pid, parent_pid, rss))
    return rows


def _wait_for_workers(root_pid: int, workers: int, timeout: float) -> None:
    if workers == 1:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            child_count = sum(
                1
                for _pid, parent_pid, _rss in _process_rows()
                if parent_pid == root_pid
            )
        except (OSError, subprocess.CalledProcessError, ValueError):
            child_count = 0
        if child_count >= workers:
            return
        time.sleep(0.05)
    raise TimeoutError(f"expected {workers} benchmark workers to start")


async def _read_until(
    stream: asyncssh.SSHReader[bytes], needle: bytes, timeout: float
) -> None:
    async def pump() -> None:
        buffer = bytearray()
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                raise RuntimeError(f"session closed before receiving {needle!r}")
            buffer.extend(chunk)
            if needle in buffer:
                return
            if len(buffer) > len(needle) * 2:
                del buffer[: -len(needle)]

    await asyncio.wait_for(pump(), timeout)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _rss_mib(root_pid: int) -> float | None:
    try:
        rows = _process_rows()
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None

    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid, _rss in rows:
            if parent_pid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return sum(rss for pid, _parent_pid, rss in rows if pid in descendants) / 1024


async def _open_session(
    port: int,
    timeout: float,
    terminal_width: int,
    terminal_height: int,
    ready_text: str,
    resize: tuple[int, int] | None,
) -> tuple[HeldSession, float]:
    started = time.perf_counter()
    connection = await asyncio.wait_for(
        asyncssh.connect(
            "127.0.0.1",
            port,
            known_hosts=None,
            username="benchmark",
        ),
        timeout,
    )
    try:
        process = await connection.create_process(
            term_type="xterm-256color",
            term_size=(terminal_width, terminal_height),
            encoding=None,
        )
        await _read_until(process.stdout, ready_text.encode(), timeout)
        if resize is not None:
            process.change_terminal_size(*resize)
    except BaseException:
        connection.close()
        await connection.wait_closed()
        raise
    return HeldSession(connection, process), time.perf_counter() - started


async def _exercise_sessions(
    sessions: list[HeldSession],
    marker: str,
    timeout: float,
    active_ratio: float,
    input_text: str | None,
    response_text: str | None,
    use_benchmark_echo: bool,
) -> list[float]:
    if active_ratio == 0 or (not use_benchmark_echo and input_text is None):
        return []
    active_count = max(1, math.ceil(len(sessions) * active_ratio))
    active = sessions[:active_count]
    start = asyncio.Event()

    async def interact(session: HeldSession) -> float:
        if use_benchmark_echo:
            session.value += marker
            outgoing = marker
            expected = f"TEXTISH_ECHO:{session.value}".encode()
        else:
            assert input_text is not None
            assert response_text is not None
            outgoing = input_text
            expected = response_text.encode()
        await start.wait()
        started = time.perf_counter()
        session.process.stdin.write(outgoing.encode())
        await _read_until(session.process.stdout, expected, timeout)
        return time.perf_counter() - started

    tasks = [asyncio.create_task(interact(session)) for session in active]
    await asyncio.sleep(0)
    start.set()
    return await asyncio.gather(*tasks)


async def _close_sessions(sessions: list[HeldSession]) -> None:
    for session in sessions:
        session.process.close()
        session.connection.close()
    await asyncio.gather(
        *(session.connection.wait_closed() for session in sessions),
        return_exceptions=True,
    )


async def _run_load(args: argparse.Namespace, port: int, server_pid: int) -> None:
    sessions: list[HeldSession] = []
    failures: list[str] = []
    rounds: list[dict[str, int | float | None]] = []
    baseline_rss = _rss_mib(server_pid)
    resize = tuple(args.resize) if args.resize is not None else None
    print(f"workers={args.workers} connect_concurrency={args.connect_concurrency}")
    print(
        "sessions  server_rss_mib  startup_p50_ms  startup_p95_ms  "
        "input_p50_ms  input_p95_ms  churn_p95_ms"
    )

    async def open_many(count: int) -> list[tuple[HeldSession, float]]:
        semaphore = asyncio.Semaphore(args.connect_concurrency)

        async def open_one() -> tuple[HeldSession, float]:
            async with semaphore:
                return await _open_session(
                    port,
                    args.timeout,
                    args.terminal_width,
                    args.terminal_height,
                    args.ready_text,
                    resize,
                )

        return await asyncio.gather(*(open_one() for _ in range(count)))

    try:
        for round_index, target in enumerate(args.sessions):
            opened = await open_many(target - len(sessions))
            sessions.extend(session for session, _latency in opened)
            startup = [latency for _session, latency in opened]
            interaction = await _exercise_sessions(
                sessions,
                chr(ord("a") + round_index % 26),
                args.timeout,
                args.active_ratio,
                args.input_text,
                args.response_text,
                args.app_ref is None,
            )
            startup_p50_ms = statistics.median(startup) * 1000
            startup_p95_ms = _percentile(startup, 0.95) * 1000
            input_p50_ms = (
                statistics.median(interaction) * 1000 if interaction else None
            )
            input_p95_ms = (
                _percentile(interaction, 0.95) * 1000 if interaction else None
            )
            rss = _rss_mib(server_pid)
            rss_text = "n/a" if rss is None else f"{rss:.1f}"

            churn_p95_ms: float | None = None
            if args.churn_ratio > 0:
                churn_count = max(1, math.ceil(target * args.churn_ratio))
                removed = sessions[-churn_count:]
                del sessions[-churn_count:]
                await _close_sessions(removed)
                replacements = await open_many(churn_count)
                sessions.extend(session for session, _latency in replacements)
                churn_p95_ms = (
                    _percentile([latency for _session, latency in replacements], 0.95)
                    * 1000
                )

            input_p50_text = "n/a" if input_p50_ms is None else f"{input_p50_ms:.1f}"
            input_p95_text = "n/a" if input_p95_ms is None else f"{input_p95_ms:.1f}"
            churn_text = "n/a" if churn_p95_ms is None else f"{churn_p95_ms:.1f}"
            print(
                f"{target:8d}  {rss_text:>14}  "
                f"{startup_p50_ms:14.1f}  "
                f"{startup_p95_ms:14.1f}  "
                f"{input_p50_text:>12}  {input_p95_text:>12}  {churn_text:>12}"
            )
            rounds.append(
                {
                    "sessions": target,
                    "server_rss_mib": rss,
                    "startup_p50_ms": startup_p50_ms,
                    "startup_p95_ms": startup_p95_ms,
                    "input_p50_ms": input_p50_ms,
                    "input_p95_ms": input_p95_ms,
                    "churn_p95_ms": churn_p95_ms,
                }
            )
            if (
                args.max_startup_p95_ms is not None
                and startup_p95_ms > args.max_startup_p95_ms
            ):
                failures.append(
                    f"{target} sessions: startup p95 {startup_p95_ms:.1f} ms "
                    f"> {args.max_startup_p95_ms:.1f} ms"
                )
            if (
                args.max_input_p95_ms is not None
                and input_p95_ms is not None
                and input_p95_ms > args.max_input_p95_ms
            ):
                failures.append(
                    f"{target} sessions: input p95 {input_p95_ms:.1f} ms "
                    f"> {args.max_input_p95_ms:.1f} ms"
                )

        soak_samples: list[float] = []
        soak_rounds = 0
        soak_deadline = time.monotonic() + args.soak_seconds
        while time.monotonic() < soak_deadline:
            soak_samples.extend(
                await _exercise_sessions(
                    sessions,
                    chr(ord("a") + soak_rounds % 26),
                    args.timeout,
                    args.active_ratio,
                    args.input_text,
                    args.response_text,
                    args.app_ref is None,
                )
            )
            soak_rounds += 1
            await asyncio.sleep(args.soak_interval)
    finally:
        final_rss = _rss_mib(server_pid)
        await _close_sessions(sessions)

    per_session = None
    if baseline_rss is not None and final_rss is not None and sessions:
        per_session = (final_rss - baseline_rss) / len(sessions)
        print(
            f"baseline_rss_mib={baseline_rss:.1f} "
            f"approx_server_mib_per_session={per_session:.3f}"
        )
    soak_p95_ms = _percentile(soak_samples, 0.95) * 1000 if soak_samples else None
    if soak_rounds and soak_p95_ms is not None:
        print(f"soak_rounds={soak_rounds} soak_input_p95_ms={soak_p95_ms:.1f}")
    elif soak_rounds:
        print(f"soak_rounds={soak_rounds} startup_only=true")

    report: dict[str, object] = {
        "workers": args.workers,
        "server_session_capacity": _session_capacity(
            args.sessions[-1], args.churn_ratio
        ),
        "app_ref": args.app_ref,
        "active_ratio": args.active_ratio,
        "churn_ratio": args.churn_ratio,
        "resize": args.resize,
        "baseline_rss_mib": baseline_rss,
        "approx_server_mib_per_session": per_session,
        "rounds": rounds,
        "soak": {
            "seconds": args.soak_seconds,
            "rounds": soak_rounds,
            "input_p95_ms": soak_p95_ms,
        },
        "failures": failures,
    }
    if args.json_output is not None:
        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if failures:
        raise RuntimeError("benchmark limits exceeded:\n" + "\n".join(failures))


def _parse_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected WIDTHxHEIGHT") from exc
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError("terminal dimensions must be positive")
    return width, height


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions",
        type=int,
        nargs="+",
        default=[100, 250, 500],
        help="Increasing live-session targets to hold and measure.",
    )
    parser.add_argument("--connect-concurrency", type=int, default=25)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--app-ref",
        help="Import reference for a real Textual app. Defaults to the echo app.",
    )
    parser.add_argument("--ready-text", default="TEXTISH_READY")
    parser.add_argument("--input-text")
    parser.add_argument("--response-text")
    parser.add_argument("--active-ratio", type=float, default=1.0)
    parser.add_argument("--churn-ratio", type=float, default=0.0)
    parser.add_argument(
        "--resize",
        type=_parse_size,
        metavar="WIDTHxHEIGHT",
        help="Send this resize after each app becomes ready.",
    )
    parser.add_argument("--soak-seconds", type=float, default=0)
    parser.add_argument("--soak-interval", type=float, default=1)
    parser.add_argument("--json-output", metavar="PATH")
    parser.add_argument("--max-startups", type=int, default=4)
    parser.add_argument("--max-pending-startups", type=int, default=64)
    parser.add_argument("--terminal-width", type=int, default=80)
    parser.add_argument("--terminal-height", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-startup-p95-ms", type=float)
    parser.add_argument("--max-input-p95-ms", type=float)
    args = parser.parse_args()

    if not args.sessions or any(target < 1 for target in args.sessions):
        parser.error("--sessions values must be positive")
    if args.sessions != sorted(set(args.sessions)):
        parser.error("--sessions values must be unique and increasing")
    if args.connect_concurrency < 1:
        parser.error("--connect-concurrency must be positive")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if not 0 <= args.active_ratio <= 1:
        parser.error("--active-ratio must be between 0 and 1")
    if not 0 <= args.churn_ratio <= 1:
        parser.error("--churn-ratio must be between 0 and 1")
    if args.soak_seconds < 0:
        parser.error("--soak-seconds must be non-negative")
    if args.soak_interval <= 0:
        parser.error("--soak-interval must be positive")
    if not args.ready_text:
        parser.error("--ready-text must not be empty")
    if args.app_ref is None and (
        args.input_text is not None or args.response_text is not None
    ):
        parser.error("--input-text and --response-text require --app-ref")
    if args.app_ref is not None and (
        (args.input_text is None) != (args.response_text is None)
    ):
        parser.error("--input-text and --response-text must be provided together")
    if args.response_text == "":
        parser.error("--response-text must not be empty")
    if args.max_input_p95_ms is not None and args.app_ref and not args.response_text:
        parser.error("--max-input-p95-ms requires an input/response scenario")
    if args.max_pending_startups < args.max_startups:
        parser.error("--max-pending-startups must be at least --max-startups")
    return args


def main() -> None:
    args = _parse_args()
    context = mp.get_context("spawn")
    port = _reserve_port()
    server = context.Process(
        target=_server_process,
        args=(
            port,
            args.workers,
            _session_capacity(args.sessions[-1], args.churn_ratio),
            args.max_startups,
            args.max_pending_startups,
            args.app_ref,
        ),
    )
    server.start()
    assert server.pid is not None
    try:
        _wait_for_port(port, server, args.timeout)
        _wait_for_workers(server.pid, args.workers, args.timeout)
        asyncio.run(_run_load(args, port, server.pid))
    finally:
        if server.is_alive():
            try:
                os.kill(server.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        server.join(10)
        if server.is_alive():
            server.terminate()
            server.join()


if __name__ == "__main__":
    main()

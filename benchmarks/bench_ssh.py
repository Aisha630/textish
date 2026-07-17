"""Benchmark real encrypted SSH connections against a textish server.

The server runs in a child process so its memory is measured separately from
the AsyncSSH clients which generate load.

Examples::

    poetry run python benchmarks/bench_ssh.py --sessions 100
    poetry run python benchmarks/bench_ssh.py --sessions 1000 --hold 5
    poetry run python benchmarks/bench_ssh.py --sessions 1000 --compression zlib
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import resource
import sys
import time
from dataclasses import dataclass
from typing import Any

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
_BENCHMARKS = os.path.dirname(__file__)
for path in (_BENCHMARKS, _SRC):
    if path not in sys.path:
        sys.path.insert(0, path)

import asyncssh  # noqa: E402
from bench_app import BenchApp  # noqa: E402

from textish.server import SessionManager, TextishSSHServer  # noqa: E402

BANNER = b"BENCH-READY"
_LAG_INTERVAL = 0.01


def _rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _ensure_file_limit(sessions: int) -> int:
    """Raise the soft descriptor limit enough for one socket per connection."""
    required = sessions + 128
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < required:
        target = required if hard == resource.RLIM_INFINITY else min(required, hard)
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        soft = target
    if soft < required:
        raise RuntimeError(
            f"file descriptor limit is {soft}, but this run needs about {required}"
        )
    return soft


async def _measure_loop_lag(stop: asyncio.Event, samples: list[float]) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        target = loop.time() + _LAG_INTERVAL
        await asyncio.sleep(_LAG_INTERVAL)
        samples.append(max(0, loop.time() - target))


async def _server_child() -> None:
    """Run the benchmark server and exchange JSON commands over stdio."""
    manager = SessionManager()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        lambda: TextishSSHServer(BenchApp, 0, manager),
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
    )
    lag_samples: list[float] = []
    stop_lag = asyncio.Event()
    lag_task = asyncio.create_task(_measure_loop_lag(stop_lag, lag_samples))
    baseline_rss = _rss_mb()
    loop_type = type(asyncio.get_running_loop())
    print(
        json.dumps(
            {
                "event": "ready",
                "port": server.get_port(),
                "loop": f"{loop_type.__module__}.{loop_type.__name__}",
            }
        ),
        flush=True,
    )

    try:
        async with server:
            while True:
                command = await asyncio.to_thread(sys.stdin.readline)
                if not command or command.strip() == "STOP":
                    break
                if command.strip() == "REPORT":
                    peak_rss = _rss_mb()
                    print(
                        json.dumps(
                            {
                                "event": "report",
                                "active_sessions": manager.active_sessions,
                                "peak_rss_mb": peak_rss,
                                "rss_delta_mb": peak_rss - baseline_rss,
                                "max_loop_lag_ms": max(lag_samples, default=0) * 1000,
                            }
                        ),
                        flush=True,
                    )
    finally:
        await manager.close_all()
        stop_lag.set()
        await lag_task


async def _read_until(stream: Any, needle: bytes, timeout: float) -> None:
    async with asyncio.timeout(timeout):
        buffer = bytearray()
        while needle not in buffer:
            chunk = await stream.read(4096)
            if not chunk:
                raise ConnectionError("SSH session closed before the app rendered")
            buffer.extend(chunk)


@dataclass
class _ClientSession:
    connection: asyncssh.SSHClientConnection
    process: Any
    rendered_at: float


def _compression_algs(mode: str) -> list[str]:
    if mode == "zlib":
        return ["zlib@openssh.com"]
    return ["none"]


async def _open_client(
    index: int,
    port: int,
    timeout: float,
    compression: str,
    semaphore: asyncio.Semaphore,
) -> _ClientSession:
    connection: asyncssh.SSHClientConnection | None = None
    async with semaphore, asyncio.timeout(timeout):
        try:
            connection = await asyncssh.connect(
                "127.0.0.1",
                port,
                username=f"bench-{index}",
                known_hosts=None,
                compression_algs=_compression_algs(compression),
            )
            process = await connection.create_process(
                term_type="xterm-256color",
                term_size=(80, 24),
                encoding=None,
            )
            await _read_until(process.stdout, BANNER, timeout)
            return _ClientSession(connection, process, time.perf_counter())
        except BaseException:
            if connection is not None:
                connection.close()
                await connection.wait_closed()
            raise


async def _child_event(
    child: asyncio.subprocess.Process, expected: str, timeout: float
) -> dict[str, Any]:
    assert child.stdout is not None
    line = await asyncio.wait_for(child.stdout.readline(), timeout)
    if not line:
        returncode = await child.wait()
        raise RuntimeError(f"benchmark server exited early with status {returncode}")
    event = json.loads(line)
    if event.get("event") != expected:
        raise RuntimeError(f"expected child event {expected!r}, got {event!r}")
    return event


async def _send_child_command(child: asyncio.subprocess.Process, command: str) -> None:
    assert child.stdin is not None
    child.stdin.write(f"{command}\n".encode())
    await child.stdin.drain()


async def _close_clients(clients: list[_ClientSession]) -> None:
    for client in clients:
        client.process.close()
        client.connection.close()
    await asyncio.gather(
        *(client.connection.wait_closed() for client in clients),
        return_exceptions=True,
    )


async def _benchmark(args: argparse.Namespace) -> bool:
    file_limit = _ensure_file_limit(args.sessions)
    env = {**os.environ, "TEXTISH_BENCH_WORK": str(args.work)}
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        os.path.abspath(__file__),
        "--server-child",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=env,
    )
    clients: list[_ClientSession] = []
    failures: list[str] = []

    try:
        ready = await _child_event(child, "ready", args.timeout)
        port = int(ready["port"])
        client_baseline_rss = _rss_mb()
        semaphore = asyncio.Semaphore(min(args.connect_concurrency, args.sessions))
        started = time.perf_counter()
        results = await asyncio.gather(
            *(
                _open_client(
                    index,
                    port,
                    args.timeout,
                    args.compression,
                    semaphore,
                )
                for index in range(args.sessions)
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                failures.append(f"{type(result).__name__}: {result}")
            else:
                clients.append(result)

        if args.hold:
            await asyncio.sleep(args.hold)

        await _send_child_command(child, "REPORT")
        report = await _child_event(child, "report", args.timeout)
        client_peak_delta = _rss_mb() - client_baseline_rss
        rendered = sorted(client.rendered_at for client in clients)
        first_ms = (rendered[0] - started) * 1000 if rendered else 0
        all_ms = (rendered[-1] - started) * 1000 if rendered else 0
        first_connection = clients[0].connection if clients else None
        cipher = (
            first_connection.get_extra_info("send_cipher")
            if first_connection is not None
            else "-"
        )
        negotiated_compression = (
            first_connection.get_extra_info("send_compression")
            if first_connection is not None
            else "-"
        )
        count = len(clients)
        server_delta = float(report["rss_delta_mb"])

        print(
            f"Real SSH: {args.sessions} connections, concurrency="
            f"{args.connect_concurrency}, hold={args.hold:g}s"
        )
        print(f"  rendered:               {count}/{args.sessions}")
        print(f"  failed:                 {len(failures)}")
        print(f"  cipher:                 {cipher}")
        print(f"  compression:            {negotiated_compression}")
        print(f"  time to first render:   {first_ms:.0f} ms")
        print(f"  time to render all:     {all_ms:.0f} ms")
        print(f"  server max loop lag:    {report['max_loop_lag_ms']:.1f} ms")
        print(f"  server peak RSS:        {report['peak_rss_mb']:.1f} MB")
        print(f"  server peak increase:   {server_delta:.1f} MB")
        print(
            f"  server memory/session:  {server_delta * 1024 / count:.0f} KB"
            if count
            else "  server memory/session:  -"
        )
        print(f"  client peak increase:   {client_peak_delta:.1f} MB")
        print(f"  active server sessions: {report['active_sessions']}")
        print(f"  server event loop:      {ready['loop']}")
        print(f"  file descriptor limit:  {file_limit}")
        for failure in failures[:5]:
            print(f"  error: {failure}")
        return not failures and int(report["active_sessions"]) == args.sessions
    finally:
        await _close_clients(clients)
        if child.returncode is None:
            try:
                await _send_child_command(child, "STOP")
                await asyncio.wait_for(child.wait(), args.timeout)
            except (BrokenPipeError, ConnectionError, TimeoutError):
                child.terminate()
                await child.wait()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=100)
    parser.add_argument("--connect-concurrency", type=int, default=100)
    parser.add_argument("--hold", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--work", type=int, default=0, help="CPU loop iters per app")
    parser.add_argument("--compression", choices=("none", "zlib"), default="none")
    parser.add_argument("--server-child", action="store_true", help=argparse.SUPPRESS)
    return parser


def _run(coro: Any) -> Any:
    """Use uvloop when installed, matching textish's blocking entry point."""
    try:
        uvloop = importlib.import_module("uvloop")
    except ImportError:
        return asyncio.run(coro)
    return asyncio.run(coro, loop_factory=uvloop.new_event_loop)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.server_child:
        _run(_server_child())
        return
    if args.sessions < 1:
        parser.error("--sessions must be at least 1")
    if args.connect_concurrency < 1:
        parser.error("--connect-concurrency must be at least 1")
    if args.hold < 0 or args.timeout <= 0 or args.work < 0:
        parser.error(
            "--hold and --work must be non-negative; --timeout must be positive"
        )
    try:
        success = _run(_benchmark(args))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

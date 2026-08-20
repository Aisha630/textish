"""
textish — serve Textual apps over SSH.

Each interactive SSH session gets a fresh Textual app instance and driver. All
sessions share one Python interpreter and asyncio event loop for low overhead.

Quickstart — import your app and serve it:

    # run.py
    from textish import serve
    from myapp import MyApp   # your Textual App, in an importable module

    serve(MyApp, port=2222)

Then ``python run.py`` and connect with ``ssh -p 2222 localhost``.
"""

import asyncio
import importlib
import logging
import multiprocessing as mp
import os
import signal
import stat
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from multiprocessing.process import BaseProcess
from pathlib import Path

from .config import AppConfig
from .metrics import MetricsCallback, MetricsSnapshot, run_metrics_reporter

# AsyncSSH is imported lazily so importing the public API remains lightweight.

log = logging.getLogger("textish")


def _default_import_paths() -> tuple[str, ...]:
    """Path entries that let a local, non-installed app import from the CLI."""
    paths: list[str] = []
    if sys.path and sys.path[0]:
        paths.append(os.path.abspath(sys.path[0]))
    cwd = os.getcwd()
    if cwd not in paths:
        paths.append(cwd)
    return tuple(paths)


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s[%(process)d]: %(message)s"
_LOG_DATEFMT = "%H:%M:%S"


def _setup_logging(
    level: int | str | None,
    color: bool = True,
    logger: logging.Logger | None = None,
) -> None:
    """Install a stderr log handler unless logging is already configured.

    Respects any existing configuration (does nothing if the target logger
    already has handlers), so callers who set up their own logging are never
    overridden. Uses coloured output via ``colorlog`` when it is installed and
    *color* is true; otherwise falls back to a plain formatter.

    Args:
        level: Log level (e.g. ``"INFO"``/``logging.DEBUG``). ``None`` disables.
        color: Prefer coloured output when ``colorlog`` is available.
        logger: Target logger (defaults to the root logger).
    """
    if level is None:
        return
    target = logger if logger is not None else logging.getLogger()
    if target.handlers:
        return

    handler = logging.StreamHandler()
    formatter: logging.Formatter | None = None
    if color:
        try:
            colorlog = importlib.import_module("colorlog")

            formatter = colorlog.ColoredFormatter(
                "%(log_color)s%(asctime)s %(levelname)-8s%(reset)s "
                "%(cyan)s%(name)s[%(process)d]%(reset)s: %(message)s",
                datefmt=_LOG_DATEFMT,
            )
        except ImportError:
            formatter = None
    handler.setFormatter(formatter or logging.Formatter(_LOG_FORMAT, _LOG_DATEFMT))
    target.addHandler(handler)
    target.setLevel(level)


def _ensure_host_key(host_key_path: str | None) -> str:
    """Return a host key path, generating a private ed25519 key with mode 0600."""
    import asyncssh

    from .config import DEFAULT_HOST_KEY_PATH

    path = Path(host_key_path or DEFAULT_HOST_KEY_PATH).expanduser()
    if path.exists():
        if not path.is_file():
            raise ValueError(f"host_key_path is not a file: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            log.warning(
                "SSH host private key %s has permissions %04o; use 0600",
                path,
                mode,
            )
        return str(path)

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_key = asyncssh.generate_private_key("ssh-ed25519").export_private_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another server created it concurrently
        return str(path)
    try:
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(private_key)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    log.info("Generated SSH host key at %s", path)
    return str(path)


def _run_event_loop(config: AppConfig, *, reuse_port: bool = False) -> None:
    """Run one listener, preferring uvloop when its optional extra exists."""
    try:
        uvloop = importlib.import_module("uvloop")
    except ImportError:
        asyncio.run(_serve_async(config, reuse_port=reuse_port))
    else:
        asyncio.run(
            _serve_async(config, reuse_port=reuse_port),
            loop_factory=uvloop.new_event_loop,
        )


def _worker_main(config: AppConfig) -> None:
    """Process entry point for one shared-port server worker."""
    try:
        _run_event_loop(config, reuse_port=True)
    except KeyboardInterrupt:
        pass


def _stop_workers(processes: list[BaseProcess], grace_period: float = 5) -> None:
    """Ask workers to shut down cleanly, then terminate stragglers."""
    for process in processes:
        if process.is_alive() and process.pid is not None:
            try:
                os.kill(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass

    deadline = time.monotonic() + grace_period
    for process in processes:
        process.join(max(0, deadline - time.monotonic()))

    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join()


def _run_server(config: AppConfig) -> None:
    """Run one listener or supervise multiple shared-port worker processes."""
    if config.workers == 1:
        _run_event_loop(config)
        return

    # Create the key once before forking so every worker presents the same key.
    worker_config = replace(
        config,
        host_key_path=_ensure_host_key(config.host_key_path),
        workers=1,
    )
    context = mp.get_context("fork")
    processes: list[BaseProcess] = [
        context.Process(
            target=_worker_main,
            args=(worker_config,),
            name=f"textish-worker-{index + 1}",
        )
        for index in range(config.workers)
    ]

    log.info("Starting %d textish workers", config.workers)
    started: list[BaseProcess] = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        while True:
            for process in started:
                process.join(0.1)
                if process.exitcode is not None:
                    raise RuntimeError(
                        f"{process.name} exited unexpectedly with "
                        f"status {process.exitcode}"
                    )
    finally:
        _stop_workers(started)


def serve(
    app: object,
    *,
    host: str = "127.0.0.1",
    port: int = 2222,
    host_key_path: str | None = None,
    workers: int = 1,
    max_connections: int = 0,
    max_ssh_connections: int = 0,
    max_authenticating: int = 64,
    max_startups: int = 4,
    max_pending_startups: int = 64,
    idle_timeout: float = 0,
    login_timeout: float = 30,
    backlog: int = 128,
    channel_window: int = 64 * 1024,
    output_buffer_limit: int = 256 * 1024,
    max_terminal_width: int = 240,
    max_terminal_height: int = 80,
    metrics_interval: float = 0,
    metrics_callback: MetricsCallback | None = None,
    auth: Callable[[str, str], bool | Awaitable[bool]] | None = None,
    log_level: int | str | None = "INFO",
    log_color: bool = True,
) -> None:
    """Serve a Textual app over SSH. Blocks until interrupted.

    Args:
        app: Your Textual ``App`` subclass, any zero-argument factory returning
             an app, or a ``"module:attr"`` import string.
        host, port: Listen address.
        host_key_path: SSH host key file. If omitted,
             ``~/.ssh/textish_host_key`` is used and generated on first run.
        workers: Server worker processes. Limits are applied per worker.
        max_connections: ``0`` means unlimited.
        max_ssh_connections: Maximum simultaneous SSH transports. ``0`` means
             unlimited. One transport may contain multiple sessions.
        max_authenticating: Maximum transports authenticating concurrently.
        max_startups: Maximum Textual apps starting concurrently.
        max_pending_startups: Maximum admitted apps not yet ready, including
             those waiting for a startup slot.
        idle_timeout: Close sessions after this many seconds without client
             input or resize activity. ``0`` disables the timeout.
        login_timeout: Seconds allowed for SSH authentication. ``0`` disables.
        backlog: Maximum queued TCP connections on the listening socket.
        channel_window: Per-session SSH receive window in bytes.
        output_buffer_limit: Buffered output bytes allowed before disconnecting
             a slow client.
        max_terminal_width, max_terminal_height: Bounds applied to client PTY
             dimensions and later resize requests.
        metrics_interval: Seconds between worker metrics snapshots. ``0``
             disables reporting.
        metrics_callback: Optional sync or async snapshot consumer. Without one,
             enabled snapshots are logged as compact JSON.
        auth: Optional public-key auth callback (see :func:`authorized_keys`).
        log_level: If set (default ``"INFO"``) and logging is not already
             configured, install a stderr log handler at this level so the
             server prints connection and lifecycle logs. Pass ``None`` to leave
             logging untouched and configure it yourself.
        log_color: Use coloured logs when the ``color`` extra is installed.

    Example::

        from textish import serve
        from myapp import MyApp
        serve(MyApp, port=2222)
    """
    _setup_logging(log_level, color=log_color)

    if not isinstance(app, str) and not callable(app):
        raise TypeError(
            "serve() expects a Textual App subclass, a zero-argument factory, "
            "or a 'module:attr' string."
        )
    config = AppConfig(
        app_ref=app if isinstance(app, str) else "",
        app_factory=None if isinstance(app, str) else app,
        host=host,
        port=port,
        host_key_path=host_key_path,
        workers=workers,
        max_connections=max_connections,
        max_ssh_connections=max_ssh_connections,
        max_authenticating=max_authenticating,
        max_startups=max_startups,
        max_pending_startups=max_pending_startups,
        idle_timeout=idle_timeout,
        login_timeout=login_timeout,
        backlog=backlog,
        channel_window=channel_window,
        output_buffer_limit=output_buffer_limit,
        max_terminal_width=max_terminal_width,
        max_terminal_height=max_terminal_height,
        metrics_interval=metrics_interval,
        metrics_callback=metrics_callback,
        auth=auth,
        import_paths=_default_import_paths(),
    )
    try:
        _run_server(config)
    except KeyboardInterrupt:
        pass


async def serve_async(config: AppConfig) -> None:
    """Start the SSH server from a validated config. Runs until cancelled.

    Use this when embedding textish in an existing asyncio program; most callers
    want the simpler blocking :func:`serve` instead.
    """
    if config.workers != 1:
        raise ValueError("serve_async() requires workers=1; use serve() for workers")
    await _serve_async(config)


async def _serve_async(config: AppConfig, *, reuse_port: bool = False) -> None:
    """Run one SSH listener, optionally sharing its port with sibling workers."""
    import asyncssh

    from .server import SessionManager, TextishSSHServer

    host_key = _ensure_host_key(config.host_key_path)
    for entry in reversed(tuple(config.import_paths)):
        if entry and entry not in sys.path:
            sys.path.insert(0, entry)
    if config.auth is None and config.host not in {"127.0.0.1", "::1", "localhost"}:
        log.warning(
            "Serving without authentication on non-loopback address %s", config.host
        )

    session_manager = SessionManager(
        max_startups=config.max_startups,
        max_pending_startups=config.max_pending_startups,
        max_ssh_connections=config.max_ssh_connections,
        max_authenticating=config.max_authenticating,
    )

    server = await asyncssh.create_server(
        lambda: TextishSSHServer(
            config.app_factory if config.app_factory is not None else config.app_ref,
            max_connections=config.max_connections,
            session_manager=session_manager,
            auth_function=config.auth,
            idle_timeout=config.idle_timeout,
            channel_window=config.channel_window,
            output_buffer_limit=config.output_buffer_limit,
            max_terminal_width=config.max_terminal_width,
            max_terminal_height=config.max_terminal_height,
        ),
        config.host,
        config.port,
        server_host_keys=[host_key],
        backlog=config.backlog,
        login_timeout=config.login_timeout,
        reuse_port=reuse_port,
    )
    metrics_task: asyncio.Task[None] | None = None
    if config.metrics_interval > 0:
        metrics_task = asyncio.create_task(
            run_metrics_reporter(
                session_manager.metrics_snapshot,
                session_manager.metrics,
                config.metrics_interval,
                config.metrics_callback,
            )
        )
    async with server:
        try:
            await server.serve_forever()
        finally:
            if metrics_task is not None:
                metrics_task.cancel()
                await asyncio.gather(metrics_task, return_exceptions=True)
            await session_manager.close_all()


def authorized_keys(path: str | Path) -> Callable[[str, str], Awaitable[bool]]:
    """Return an auth function that allows connections whose public key appears
    in an OpenSSH ``authorized_keys`` file.

    The returned callable is compatible with `AppConfig.auth`.

    The file is re-read on every authentication attempt so changes take effect
    without restarting the server.

    Args:
        path: Path to the ``authorized_keys`` file (``~`` is expanded).

    Example::

        serve(MyApp, auth=authorized_keys("~/.ssh/authorized_keys"))
    """
    resolved = Path(path).expanduser()

    async def _auth(_username: str, public_key_str: str) -> bool:
        import asyncssh

        try:
            text = await asyncio.to_thread(resolved.read_text)
        except OSError:
            log.warning("Could not read authorized_keys file: %s", resolved)
            return False

        try:
            incoming_key = asyncssh.import_public_key(public_key_str)
            keys = asyncssh.import_authorized_keys(text)
        except (KeyError, TypeError, ValueError):
            return False
        # Empty host values allow non-host-related options such as ``restrict``.
        # Host-based restrictions fail closed because this callback intentionally
        # has no client-address argument.
        return keys.validate(incoming_key, "", "") is not None

    return _auth


__all__ = [
    "serve",
    "serve_async",
    "AppConfig",
    "MetricsCallback",
    "MetricsSnapshot",
    "authorized_keys",
]

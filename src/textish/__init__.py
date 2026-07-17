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
import os
import stat
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from .config import AppConfig

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


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
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
                "%(cyan)s%(name)s%(reset)s: %(message)s",
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


def _run_server(config: AppConfig) -> None:
    """Run the async server, preferring uvloop when its optional extra exists."""
    try:
        uvloop = importlib.import_module("uvloop")
    except ImportError:
        asyncio.run(serve_async(config))
    else:
        asyncio.run(serve_async(config), loop_factory=uvloop.new_event_loop)


def serve(
    app: object,
    *,
    host: str = "127.0.0.1",
    port: int = 2222,
    host_key_path: str | None = None,
    max_connections: int = 0,
    idle_timeout: float = 0,
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
        max_connections: ``0`` means unlimited.
        idle_timeout: Close sessions after this many seconds without client
             input or resize activity. ``0`` disables the timeout.
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
        max_connections=max_connections,
        idle_timeout=idle_timeout,
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

    session_manager = SessionManager()

    server = await asyncssh.create_server(
        lambda: TextishSSHServer(
            config.app_factory if config.app_factory is not None else config.app_ref,
            max_connections=config.max_connections,
            session_manager=session_manager,
            auth_function=config.auth,
            idle_timeout=config.idle_timeout,
        ),
        config.host,
        config.port,
        server_host_keys=[host_key],
    )
    async with server:
        try:
            await server.serve_forever()
        finally:
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


__all__ = ["serve", "serve_async", "AppConfig", "authorized_keys"]

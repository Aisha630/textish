"""
textish — serve Textual apps over SSH.

Each incoming SSH connection runs the Textual app in its own subinterpreter
(its own module state and, on Python 3.14+, its own GIL), bridged to the SSH
channel. Requires Python 3.14+.

Quickstart — import your app and serve it:

    # run.py
    from textish import serve
    from myapp import MyApp   # your Textual App, in an importable module

    serve(MyApp, port=2222)

Then ``python run.py`` and connect with ``ssh -p 2222 localhost``.
"""

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from .config import AppConfig

# NOTE: asyncssh (and its cryptography/Rust dependency) is imported lazily inside
# the functions below, never at module top level. Importing ``textish`` must stay
# free of asyncssh so the subinterpreter worker — which imports
# ``textish.subinterp._worker`` and therefore runs this package's ``__init__`` —
# does not drag in cryptography, whose Rust bindings cannot load in a
# subinterpreter. See tests/unit/test_import_safety.py.

log = logging.getLogger("textish")


def _resolve_app(app: object) -> str:
    """Derive the ``module:qualname`` import ref for *app*.

    Accepts an ``App`` subclass, any zero-argument factory, or a ready
    ``"module:attr"`` string. Rejects objects defined in ``__main__`` because a
    subinterpreter cannot import the main script.
    """
    if isinstance(app, str):
        return app
    module = getattr(app, "__module__", "")
    qualname = getattr(app, "__qualname__", "")
    if not module or not qualname:
        raise TypeError(
            "serve() expects a Textual App subclass, a zero-argument factory, "
            "or a 'module:attr' string."
        )
    if module == "__main__":
        raise ValueError(
            "Define your app in an importable module and import it, e.g. "
            "`from myapp import MyApp; serve(MyApp)`. It cannot live in the "
            "script you run directly, because each connection re-imports it in a "
            "fresh subinterpreter that has no '__main__'."
        )
    return f"{module}:{qualname}"


def _default_import_paths() -> tuple[str, ...]:
    """Path entries that let a local (non-installed) app import in a subinterp.

    A fresh subinterpreter does not inherit the parent's script directory, so we
    forward the running script's directory and the current working directory.
    """
    paths: list[str] = []
    if sys.path and sys.path[0]:
        paths.append(os.path.abspath(sys.path[0]))
    cwd = os.getcwd()
    if cwd not in paths:
        paths.append(cwd)
    return tuple(paths)


def _ensure_host_key(host_key_path: str | None) -> str:
    """Return a host key path, generating an ed25519 key if none exists."""
    import asyncssh

    path = Path(host_key_path).expanduser() if host_key_path else Path("ssh_host_key")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(path))
        log.info("Generated SSH host key at %s", path)
    return str(path)


def serve(
    app: object,
    *,
    host: str = "0.0.0.0",
    port: int = 2222,
    host_key_path: str | None = None,
    max_connections: int = 0,
    auth: Callable[[str, str], bool | Awaitable[bool]] | None = None,
) -> None:
    """Serve a Textual app over SSH. Blocks until interrupted.

    Args:
        app: Your Textual ``App`` subclass, a zero-argument factory returning an
             app, or a ``"module:attr"`` import string. It must live in an
             importable module (not the ``__main__`` script).
        host, port: Listen address.
        host_key_path: SSH host key file. If omitted, ``./ssh_host_key`` is used
             and generated on first run.
        max_connections: ``0`` means unlimited.
        auth: Optional public-key auth callback (see :func:`authorized_keys`).

    Example::

        from textish import serve
        from myapp import MyApp
        serve(MyApp, port=2222)
    """
    config = AppConfig(
        app_ref=_resolve_app(app),
        host=host,
        port=port,
        host_key_path=_ensure_host_key(host_key_path),
        max_connections=max_connections,
        auth=auth,
        import_paths=_default_import_paths(),
    )
    try:
        import uvloop

        asyncio.run(serve_async(config), loop_factory=uvloop.new_event_loop)
    except ImportError:
        asyncio.run(serve_async(config))
    except KeyboardInterrupt:
        pass


async def serve_async(config: AppConfig) -> None:
    """Start the SSH server from a validated config. Runs until cancelled.

    Use this when embedding textish in an existing asyncio program; most callers
    want the simpler blocking :func:`serve` instead.
    """
    import asyncssh

    from .server import SessionManager, TextishSSHServer

    # Track connections for graceful shutdown and max_connections enforcement.
    active_connections: set[asyncssh.SSHServerConnection] = set()
    session_manager = SessionManager()

    server = await asyncssh.create_server(
        lambda: TextishSSHServer(
            config.app_ref,
            max_connections=config.max_connections,
            active_connections=active_connections,
            session_manager=session_manager,
            auth_function=config.auth,
            import_paths=tuple(config.import_paths),
        ),
        config.host,
        config.port,
        server_host_keys=list(config.host_keys),
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
        try:
            text = await asyncio.to_thread(resolved.read_text)
        except OSError:
            log.warning("Could not read authorized_keys file: %s", resolved)
            return False

        # The key blob (second whitespace-separated field) is the canonical
        # identity of the key
        parts = public_key_str.split()
        if len(parts) < 2:
            return False
        incoming_blob = parts[1]

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) >= 2 and fields[1] == incoming_blob:
                return True
        return False

    return _auth


__all__ = ["serve", "serve_async", "AppConfig", "authorized_keys"]

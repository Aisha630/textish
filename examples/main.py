"""Minimal example: serve a Textual app over SSH with textish.

Each connection runs the app in its own subinterpreter (Python 3.14+). The app
is referenced by import path, ``module:attr``, and must be importable inside the
subinterpreter. Real apps are usually pip-installed, so their dotted path just
works. This bundled example is not installed, so run it from the repo root with
the repo root on PYTHONPATH (subinterpreters honor PYTHONPATH)::

    PYTHONPATH=. poetry run python -m examples.main
    # then, from another terminal:
    ssh -p 2222 localhost

Connect with a terminal (a PTY is required).
"""

import asyncio
from pathlib import Path

import asyncssh

from textish import AppConfig, serve

HOST_KEY = Path("ssh_host_key")


def _ensure_host_key() -> str:
    if not HOST_KEY.exists():
        asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(HOST_KEY))
    return str(HOST_KEY)


async def main() -> None:
    config = AppConfig(
        app_ref="examples.app:WordleApp",  # importable as a package from repo root
        host="127.0.0.1",
        port=2222,
        host_key_path=_ensure_host_key(),
    )
    await serve(config)


if __name__ == "__main__":
    asyncio.run(main())

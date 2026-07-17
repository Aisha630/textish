"""Minimal example: serve a Textual app over SSH with textish.

Each connection runs the app in its own subinterpreter (Python 3.14+). The app
is referenced by import path, ``module:attr``, and must be importable from where
the server runs. Here we serve ``WordleApp`` from ``examples/app.py``, so run
this from the ``examples`` directory::

    cd examples && python main.py
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
        app_ref="app:WordleApp",  # examples/app.py -> WordleApp
        host="127.0.0.1",
        port=2222,
        host_key_path=_ensure_host_key(),
    )
    await serve(config)


if __name__ == "__main__":
    asyncio.run(main())

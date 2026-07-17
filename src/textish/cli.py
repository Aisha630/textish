"""Command-line interface for textish.

Invoked as ``textish <app_ref> [options]`` after installation, where
``app_ref`` is the import path of a Textual app (``package.module:attr``).
"""

import argparse
import asyncio
import logging
import os
import sys

import uvloop

from . import _default_import_paths, _ensure_host_key, authorized_keys, serve_async
from .config import AppConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="textish",
        description="Serve a Textual app over SSH (one subinterpreter per client).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "app_ref",
        help='Import path of your Textual app, e.g. "my_package.my_module:MyApp". '
        "It must be importable from where the server runs.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Address to listen on.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=2222,
        help="TCP port to listen on.",
    )
    parser.add_argument(
        "--host-key",
        metavar="PATH",
        default=None,
        dest="host_key_path",
        help="Path to the SSH host key file. Defaults to ~/.ssh/ssh_host_key.",
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=0,
        metavar="N",
        help="Maximum simultaneous SSH sessions. 0 means unlimited.",
    )
    parser.add_argument(
        "--authorized-keys",
        metavar="PATH",
        default=None,
        dest="authorized_keys",
        help="Path to an OpenSSH authorized_keys file. Only listed keys are allowed.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    auth = authorized_keys(args.authorized_keys) if args.authorized_keys else None

    try:
        config = AppConfig(
            app_ref=args.app_ref,
            host=args.host,
            port=args.port,
            host_key_path=_ensure_host_key(args.host_key_path),
            max_connections=args.max_connections,
            auth=auth,
            # cwd first so a local module referenced by app_ref imports.
            import_paths=(os.getcwd(), *_default_import_paths()),
        )
    except ValueError as e:
        parser.error(str(e))

    print(
        f"Serving on {config.host}:{config.port}, "
        f"connect with: ssh -p {config.port} {config.host}"
    )

    try:
        asyncio.run(serve_async(config), loop_factory=uvloop.new_event_loop)
    except OSError as e:
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        pass

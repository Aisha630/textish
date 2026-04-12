"""Command-line interface for textish.

Invoked as ``textish <app_command> [options]`` after installation.
"""

import argparse
import sys

from .config import AppConfig
from . import serve_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="textish",
        description="Serve a Textual app over SSH.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "app_command",
        help='Shell command that launches your Textual app, e.g. "python my_app.py".',
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
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        config = AppConfig(
            app_command=args.app_command,
            host=args.host,
            port=args.port,
            host_key_path=args.host_key_path,
            max_connections=args.max_connections,
        )
    except ValueError as e:
        parser.error(str(e))

    print(f"Serving on {config.host}:{config.port} — connect with: ssh -p {config.port} {config.host}")

    try:
        serve_config(config)
    except OSError as e:
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        pass

"""Command-line interface for textish.

Invoked as ``textish <app_command> [options]`` after installation.
"""

import argparse
import asyncio
import logging
import sys

from . import authorized_keys, serve
from .config import AppConfig


def _parse_env_var(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected KEY=VALUE")
    key, env_value = value.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError("environment variable name must not be empty")
    return key, env_value


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
    parser.add_argument(
        "--authorized-keys",
        metavar="PATH",
        default=None,
        dest="authorized_keys",
        help="Path to an OpenSSH authorized_keys file. Only listed keys are allowed.",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        type=_parse_env_var,
        metavar="KEY=VALUE",
        help="Environment variable to pass to the app. Can be repeated.",
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
            app_command=args.app_command,
            host=args.host,
            port=args.port,
            host_key_path=args.host_key_path,
            max_connections=args.max_connections,
            env=dict(args.env),
            auth=auth,
        )
    except ValueError as e:
        parser.error(str(e))

    print(
        f"Serving on {config.host}:{config.port} — connect with: ssh -p ",
        f"{config.port} {config.host}",
    )

    try:
        asyncio.run(serve(config))
    except OSError as e:
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        pass

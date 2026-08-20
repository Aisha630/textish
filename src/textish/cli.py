"""Command-line interface for textish.

Invoked as ``textish <app_ref> [options]`` after installation, where
``app_ref`` is the import path of a Textual app (``package.module:attr``).
"""

import argparse
import sys

from . import (
    _default_import_paths,
    _run_server,
    _setup_logging,
    authorized_keys,
)
from .config import AppConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="textish",
        description="Serve a Textual app over SSH (one app instance per session).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "app_ref",
        help='Import path of your Textual app, e.g. "my_package.my_module:MyApp". '
        "It must be importable from where the server runs.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
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
        default=argparse.SUPPRESS,
        dest="host_key_path",
        help="Path to the SSH host key file. Defaults to ~/.ssh/textish_host_key.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Server worker processes. Resource limits apply per worker.",
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=0,
        metavar="N",
        help="Maximum simultaneous SSH sessions. 0 means unlimited.",
    )
    parser.add_argument(
        "--max-ssh-connections",
        type=int,
        default=0,
        metavar="N",
        help="Maximum simultaneous SSH transports. 0 means unlimited.",
    )
    parser.add_argument(
        "--max-authenticating",
        type=int,
        default=64,
        metavar="N",
        help="Maximum SSH transports authenticating concurrently.",
    )
    parser.add_argument(
        "--max-startups",
        type=int,
        default=4,
        metavar="N",
        help="Maximum Textual apps starting concurrently.",
    )
    parser.add_argument(
        "--max-pending-startups",
        type=int,
        default=64,
        metavar="N",
        help="Maximum admitted apps which have not reached ready state.",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=0,
        metavar="SECONDS",
        help="Close sessions idle for this long. 0 disables the timeout.",
    )
    parser.add_argument(
        "--login-timeout",
        type=float,
        default=30,
        metavar="SECONDS",
        help="Maximum time allowed for SSH authentication. 0 disables it.",
    )
    parser.add_argument(
        "--backlog",
        type=int,
        default=128,
        metavar="N",
        help="Maximum queued TCP connections on the listening socket.",
    )
    parser.add_argument(
        "--channel-window",
        type=int,
        default=64 * 1024,
        metavar="BYTES",
        help="Per-session SSH receive window.",
    )
    parser.add_argument(
        "--output-buffer-limit",
        type=int,
        default=256 * 1024,
        metavar="BYTES",
        help="Disconnect slow clients after this much buffered output.",
    )
    parser.add_argument(
        "--max-terminal-width",
        type=int,
        default=240,
        metavar="COLUMNS",
        help="Maximum accepted PTY width.",
    )
    parser.add_argument(
        "--max-terminal-height",
        type=int,
        default=80,
        metavar="ROWS",
        help="Maximum accepted PTY height.",
    )
    parser.add_argument(
        "--metrics-interval",
        type=float,
        default=0,
        metavar="SECONDS",
        help="Log per-worker metrics as JSON at this interval. 0 disables it.",
    )
    parser.add_argument(
        "--authorized-keys",
        metavar="PATH",
        default=None,
        dest="authorized_keys",
        help="Path to an OpenSSH authorized_keys file. Only listed keys are allowed.",
    )
    parser.add_argument(
        "--log-level",
        default=argparse.SUPPRESS,
        metavar="LEVEL",
        help="Log level (DEBUG, INFO, WARNING, ...). Overrides -v. (default: INFO).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable coloured log output.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Shortcut for --log-level DEBUG.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    level = getattr(args, "log_level", None) or ("DEBUG" if args.verbose else "INFO")
    _setup_logging(level, color=not args.no_color)

    auth = authorized_keys(args.authorized_keys) if args.authorized_keys else None

    try:
        config = AppConfig(
            app_ref=args.app_ref,
            host=args.host,
            port=args.port,
            host_key_path=getattr(args, "host_key_path", None),
            workers=args.workers,
            max_connections=args.max_connections,
            max_ssh_connections=args.max_ssh_connections,
            max_authenticating=args.max_authenticating,
            max_startups=args.max_startups,
            max_pending_startups=args.max_pending_startups,
            idle_timeout=args.idle_timeout,
            login_timeout=args.login_timeout,
            backlog=args.backlog,
            channel_window=args.channel_window,
            output_buffer_limit=args.output_buffer_limit,
            max_terminal_width=args.max_terminal_width,
            max_terminal_height=args.max_terminal_height,
            metrics_interval=args.metrics_interval,
            auth=auth,
            import_paths=_default_import_paths(),
        )
    except ValueError as e:
        parser.error(str(e))

    print(
        f"Serving on {config.host}:{config.port} with {config.workers} worker(s), "
        f"connect with: ssh -p {config.port} "
        f"{'localhost' if config.host in {'0.0.0.0', '::'} else config.host}"
    )

    try:
        _run_server(config)
    except OSError as e:
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        pass

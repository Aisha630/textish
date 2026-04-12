from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class AppConfig:
    host: str = "0.0.0.0"
    port: int = 2222
    app_command: str = "python examples/app.py"
    host_key_path: str | None = None
    max_connections: int = 0  # infinite connections
    auth: Callable[[str, str], bool | Awaitable[bool]] | None = (
        # auth(username, public_key_str) -> True if valid, False otherwise.
        # If None, allows all logins without authentication.
        None
    )

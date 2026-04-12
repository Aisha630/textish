from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    """Configuration for the textish SSH server.

    Pass an instance to :func:`~textish.serve_config` as an alternative to
    passing keyword arguments directly to :func:`~textish.serve`.

    Attributes:
        host:            Address to listen on. Defaults to all interfaces.
        port:            TCP port to listen on. Defaults to 2222.
        app_command:     Shell command that launches the Textual app.
        host_key_path:   Path to the SSH host key file. If ``None``, textish
                         falls back to ``~/.ssh/ssh_host_key``.
        max_connections: Maximum number of simultaneous SSH sessions.
                         ``0`` means unlimited.
        auth:            Optional public-key auth callback.
                         Signature: ``(username, public_key_str) -> bool``.
                         May also be async. ``None`` allows all logins without
                         authentication.
    """

    host: str = "0.0.0.0"
    port: int = 2222
    app_command: str = "python examples/app.py"
    host_key_path: str | None = None
    max_connections: int = 0
    auth: Callable[[str, str], bool | Awaitable[bool]] | None = None

    def __post_init__(self) -> None:
        if not self.host or not self.host.strip():
            raise ValueError("host must not be empty")
        if not (1 <= self.port <= 65535):
            raise ValueError(f"port must be between 1 and 65535, got {self.port}")
        if not self.app_command or not self.app_command.strip():
            raise ValueError("app_command must not be empty")
        if self.max_connections < 0:
            raise ValueError(
                f"max_connections must be >= 0 (0 means unlimited), "
                f"got {self.max_connections}"
            )
        if self.host_key_path is not None:
            path = Path(self.host_key_path).expanduser()
            if not path.exists():
                raise ValueError(f"host_key_path does not exist: {self.host_key_path}")
            if not path.is_file():
                raise ValueError(f"host_key_path is not a file: {self.host_key_path}")

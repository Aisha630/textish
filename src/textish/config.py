from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST_KEY_PATH = "~/.ssh/ssh_host_key"


@dataclass
class AppConfig:
    """Configuration for the textish SSH server.

    Pass an instance to :func:`~textish.serve` for validated configuration
    as an object.

    Attributes:
        host:            Address to listen on. Defaults to all interfaces.
        port:            TCP port to listen on. Defaults to 2222.
        app_ref:         Import path of the Textual app to serve, in the form
                         ``"package.module:attr"`` where ``attr`` is a
                         zero-argument callable (usually an ``App`` subclass)
                         returning a fresh app. The app is imported and
                         constructed inside a per-connection subinterpreter, so
                         it must be importable by that path.
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
    app_ref: str = ""
    host_key_path: str | None = None
    max_connections: int = 0
    auth: Callable[[str, str], bool | Awaitable[bool]] | None = None

    @property
    def host_keys(self) -> tuple[str, ...]:
        """SSH host key paths to pass to asyncssh."""
        path = self.host_key_path or DEFAULT_HOST_KEY_PATH
        return (str(Path(path).expanduser()),)

    def __post_init__(self) -> None:
        if not self.host or not self.host.strip():
            raise ValueError("host must not be empty")
        if not (1 <= self.port <= 65535):
            raise ValueError(f"port must be between 1 and 65535, got {self.port}")
        if not self.app_ref or not self.app_ref.strip():
            raise ValueError("app_ref must not be empty")
        module, sep, attr = self.app_ref.partition(":")
        if not sep or not module.strip() or not attr.strip():
            raise ValueError(
                f"app_ref must be of the form 'package.module:attr', "
                f"got {self.app_ref!r}"
            )
        if self.max_connections < 0:
            raise ValueError(
                f"max_connections must be >= 0 (0 means unlimited), "
                f"got {self.max_connections}"
            )
        path = Path(self.host_keys[0])
        if not path.exists():
            raise ValueError(f"host_key_path does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"host_key_path is not a file: {path}")

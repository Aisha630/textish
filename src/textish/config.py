from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOST_KEY_PATH = "~/.ssh/textish_host_key"


@dataclass
class AppConfig:
    """Configuration for the textish SSH server.

    Pass an instance to :func:`~textish.serve_async` when embedding textish in
    an existing event loop.

    Attributes:
        host:            Address to listen on. Defaults to localhost.
        port:            TCP port to listen on. Defaults to 2222.
        app_ref:         Optional import path of the Textual app to serve, in the form
                         ``"package.module:attr"`` where ``attr`` is a
                         zero-argument callable (usually an ``App`` subclass)
                         returning a fresh app. The app is imported and
                         constructed for each SSH session.
        app_factory:     Optional zero-argument callable used directly in the
                         shared interpreter. Exactly one of this or ``app_ref``
                         must be provided.
        host_key_path:   Path to the SSH host key file. If ``None``, textish
                         uses ``~/.ssh/textish_host_key``. Missing keys are
                         generated securely when the server starts.
        max_connections: Maximum number of simultaneous SSH sessions.
                         ``0`` means unlimited.
        idle_timeout:    Seconds without client activity before a session is
                         closed. ``0`` disables the timeout.
        auth:            Optional public-key auth callback.
                         Signature: ``(username, public_key_str) -> bool``.
                         May also be async. ``None`` allows all logins without
                         authentication.
        import_paths:    Extra ``sys.path`` entries prepended on server startup
                         so ``app_ref`` is importable
                         (e.g. the directory of a local, non-installed app).
                         Usually filled in for you by :func:`~textish.serve`.
    """

    host: str = "127.0.0.1"
    port: int = 2222
    app_ref: str = ""
    app_factory: Callable[[], object] | None = field(default=None, repr=False)
    host_key_path: str | None = None
    max_connections: int = 0
    idle_timeout: float = 0
    auth: Callable[[str, str], bool | Awaitable[bool]] | None = None
    import_paths: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.host or not self.host.strip():
            raise ValueError("host must not be empty")
        if not (1 <= self.port <= 65535):
            raise ValueError(f"port must be between 1 and 65535, got {self.port}")
        if self.app_factory is not None:
            if self.app_ref:
                raise ValueError("provide app_ref or app_factory, not both")
            if not callable(self.app_factory):
                raise TypeError("app_factory must be callable")
        else:
            if not self.app_ref or not self.app_ref.strip():
                raise ValueError(
                    "app_ref must not be empty when app_factory is omitted"
                )
            module, sep, attr = self.app_ref.partition(":")
            module_parts = module.split(".")
            attr_parts = attr.split(".")
            if (
                not sep
                or ":" in attr
                or not all(part.isidentifier() for part in module_parts)
                or not all(part.isidentifier() for part in attr_parts)
                or "<locals>" in attr
            ):
                raise ValueError(
                    f"app_ref must be of the form 'package.module:attr', "
                    f"got {self.app_ref!r}"
                )
        if self.max_connections < 0:
            raise ValueError(
                f"max_connections must be >= 0 (0 means unlimited), "
                f"got {self.max_connections}"
            )
        if self.idle_timeout < 0:
            raise ValueError("idle_timeout must be >= 0 (0 disables it)")
        if self.auth is not None and not callable(self.auth):
            raise TypeError("auth must be callable")
        path = Path(self.host_key_path or DEFAULT_HOST_KEY_PATH).expanduser()
        if path.exists() and not path.is_file():
            raise ValueError(f"host_key_path is not a file: {path}")

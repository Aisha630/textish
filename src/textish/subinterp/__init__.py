"""The textish backend: one subinterpreter per connection (Python 3.14+).

Runs each connection's Textual app in its own subinterpreter, giving separate
module state and its own GIL (so sessions render in parallel across cores) while
staying far cheaper than a subprocess. Bytes cross between the main interpreter
(which owns asyncssh) and each subinterpreter (which owns only Textual) over
cross-interpreter queues.

Requires Python 3.14+.

See :class:`~textish.subinterp.session.SubinterpAppSession`.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from textish.subinterp.session import SubinterpAppSession

__all__ = ["SubinterpAppSession", "SUBINTERP_AVAILABLE"]


def __getattr__(name: str) -> Any:
    # Imported lazily so that importing ``textish.subinterp`` (which happens when
    # the subinterpreter loads ``textish.subinterp._worker``) does NOT import
    # ``session``, which imports asyncssh. asyncssh's cryptography dependency
    # cannot load in a subinterpreter. The worker only needs ``_worker``, not
    # ``session``. See tests/unit/test_import_safety.py.
    if name in ("SubinterpAppSession", "SUBINTERP_AVAILABLE"):
        from textish.subinterp import session

        return getattr(session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

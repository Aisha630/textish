"""The textish backend: one subinterpreter per connection (Python 3.14+).

Runs each connection's Textual app in its own subinterpreter, giving separate
module state and its own GIL (so sessions render in parallel across cores) while
staying far cheaper than a subprocess. Bytes cross between the main interpreter
(which owns asyncssh) and each subinterpreter (which owns only Textual) over
cross-interpreter queues.

Requires Python 3.14+.

See :class:`~textish.subinterp.session.SubinterpAppSession`.
"""

from textish.subinterp.session import SUBINTERP_AVAILABLE, SubinterpAppSession

__all__ = ["SubinterpAppSession", "SUBINTERP_AVAILABLE"]

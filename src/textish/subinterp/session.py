"""Main-interpreter side of the subinterpreter backend.

``SubinterpAppSession`` runs one connection's Textual app in its own
subinterpreter (on its own OS thread, with its own GIL on Python 3.14+) and
bridges bytes between that subinterpreter and the SSH channel over two
cross-interpreter queues.

Characteristics:

* Isolation: each session has separate module state and its own GIL, so
  sessions render in parallel across cores and a Python-level failure in one is
  far less likely to disturb another.
* Cost: a subinterpreter re-imports modules, so expect a few MB per session,
  still far below a full subprocess (tens of MB per interpreter).

Security note: per the CPython docs, subinterpreters are *not* a security
boundary. A malicious C extension can still cross interpreters, and a hard crash
takes the whole process down. For untrusted code use the hardened subprocess
backend instead.

Requires Python 3.14+ (``concurrent.interpreters``). ``SUBINTERP_AVAILABLE``
reports whether the runtime supports it.
"""

from __future__ import annotations

import asyncio
import logging

import asyncssh

from textish.subinterp._worker import run_app

log = logging.getLogger("textish")

try:  # 3.14+ only
    from concurrent import interpreters as _interpreters

    SUBINTERP_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on runtime version
    _interpreters = None  # type: ignore[assignment]
    SUBINTERP_AVAILABLE = False

# Poll interval for draining the outbound queue without an executor thread.
_POLL = 0.005


class SubinterpAppSession:
    """Run one SSH client's Textual app in a dedicated subinterpreter."""

    def __init__(
        self,
        app_ref: str,
        channel: asyncssh.SSHServerChannel[bytes],
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        """
        Args:
            app_ref: ``"package.module:attr"`` naming a zero-argument callable
                     (usually an ``App`` subclass) that returns a fresh app.
                     Resolved and constructed inside the subinterpreter.
            channel: asyncssh channel for bidirectional terminal bytes.
            cols:    Initial terminal width in columns.
            rows:    Initial terminal height in rows.
        """
        if not SUBINTERP_AVAILABLE:
            raise RuntimeError(
                "SubinterpAppSession requires Python 3.14+ "
                "(concurrent.interpreters is unavailable)."
            )
        self._app_ref = app_ref
        self._channel = channel
        self._cols = cols
        self._rows = rows
        self._in_q: object | None = None
        self._out_q: object | None = None
        self._interp: object | None = None
        self._thread: object | None = None

    async def run(self) -> None:
        """Spawn the subinterpreter and pump its output to the SSH channel.

        Runs until the app exits, the client disconnects, or an error occurs.
        The channel is always closed and the subinterpreter torn down in the
        ``finally`` block.
        """
        import queue  # cross-interpreter Queue raises stdlib queue.Empty subclasses

        self._in_q = _interpreters.create_queue()
        self._out_q = _interpreters.create_queue()
        self._interp = _interpreters.create()
        # call_in_thread runs run_app in the subinterpreter on a new OS thread.
        # The app named by app_ref must be importable inside the subinterpreter
        # (an installed package, or a module on PYTHONPATH).
        self._thread = self._interp.call_in_thread(
            run_app, self._app_ref, self._in_q, self._out_q, self._cols, self._rows
        )

        try:
            while True:
                try:
                    msg = self._out_q.get_nowait()
                except queue.Empty:
                    if not self._thread.is_alive():
                        break
                    await asyncio.sleep(_POLL)
                    continue
                tag = msg[0]
                if tag == "D":
                    self._safe_write(msg[1])
                elif tag == "error":
                    log.warning("subinterp app error: %s", msg[1])
                    break
                elif tag == "done":
                    break
        finally:
            await self._teardown()

    def _safe_write(self, data: bytes) -> None:
        try:
            self._channel.write(data)
        except (BrokenPipeError, ConnectionError, OSError):
            pass

    async def _teardown(self) -> None:
        self._channel.close()
        if self._in_q is not None:
            try:
                self._in_q.put(("X",))
            except Exception:  # pragma: no cover - queue may be gone
                pass
        # Give the worker a moment to unwind, then reclaim the interpreter.
        thread = self._thread
        if thread is not None:
            for _ in range(400):
                if not thread.is_alive():
                    break
                await asyncio.sleep(0.01)
        if self._interp is not None:
            try:
                self._interp.close()
            except Exception:  # pragma: no cover - already gone / still busy
                log.debug("interp.close() failed", exc_info=True)

    async def send_input(self, data: bytes) -> None:
        """Forward raw input bytes from the SSH client to the subinterpreter."""
        if self._in_q is not None:
            self._in_q.put(("D", data))

    async def resize(self, cols: int, rows: int) -> None:
        """Notify the app that the client terminal was resized."""
        self._cols = cols
        self._rows = rows
        if self._in_q is not None:
            self._in_q.put(("R", cols, rows))

    async def close(self) -> None:
        """Ask the subinterpreter app to exit cleanly. Idempotent."""
        if self._in_q is not None:
            try:
                self._in_q.put(("X",))
            except Exception:  # pragma: no cover
                pass

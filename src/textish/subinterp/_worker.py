"""Code that runs *inside* a subinterpreter to host one Textual app.

This module is imported fresh inside each subinterpreter (its own ``sys.modules``,
its own GIL on Python 3.14+). It must stay pure Python and must NOT import
asyncssh, uvloop, or cryptography: those C-heavy libraries stay in the main
interpreter. Only Textual and this driver run in the subinterpreter.

Communication with the main interpreter is by two cross-interpreter queues
(``concurrent.interpreters.Queue``), which carry only picklable messages:

    inbound  (main -> worker):  ("D", data: bytes)         keystrokes
                                ("R", cols: int, rows: int) resize
                                ("X",)                       exit
    outbound (worker -> main):  ("D", data: bytes)          app output
                                ("done",)                    app exited cleanly
                                ("error", text: str)         app raised
"""

from __future__ import annotations

from codecs import getincrementaldecoder
from typing import Any

from textual import events
from textual._xterm_parser import XTermParser
from textual.driver import Driver
from textual.geometry import Size


class QueueDriver(Driver):
    """A Textual driver whose terminal is a cross-interpreter queue.

    Replaces Textual's terminal assumptions (stdin/stdout, termios, SIGWINCH)
    with queue I/O. Because it runs in a subinterpreter that cannot touch the
    SSH channel object, it writes output as ``("D", bytes)`` messages onto an
    outbound queue and is fed input by the worker's input pump.
    """

    _out_put: Any  # bound per-connection: outbound_queue.put

    def __init__(
        self,
        app: Any,
        *,
        debug: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(app, debug=debug, mouse=mouse, size=size)
        self._parser = XTermParser(debug=debug)
        self._decoder = getincrementaldecoder("utf-8")()
        self._alive = False

    # -- output ---------------------------------------------------------------

    def write(self, data: str) -> None:
        self._out_put(("D", data.encode("utf-8", errors="replace")))

    def flush(self) -> None:
        pass

    # -- input ----------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        if not self._alive:
            return
        text = self._decoder.decode(data)
        if not text:
            return
        for event in self._parser.feed(text):
            self.process_message(event)

    # -- lifecycle ------------------------------------------------------------

    def start_application_mode(self) -> None:
        self._alive = True
        self.write("\x1b[?1049h")  # enter alternate screen
        for seq in ("\x1b[?1000h", "\x1b[?1003h", "\x1b[?1015h", "\x1b[?1006h"):
            self.write(seq)  # mouse
        self.write("\x1b[?25l")  # hide cursor
        self.write("\x1b[?2004h")  # bracketed paste
        size = Size(80, 24) if self._size is None else Size(*self._size)
        self._post(events.Resize(size, size))
        self.flush()
        self._app.call_later(self._app.post_message, events.AppFocus())

    def disable_input(self) -> None:
        self._alive = False

    def stop_application_mode(self) -> None:
        self._alive = False
        self.write("\x1b[?2004l")
        for seq in ("\x1b[?1000l", "\x1b[?1003l", "\x1b[?1015l", "\x1b[?1006l"):
            self.write(seq)
        self.write("\x1b[?25h")  # show cursor
        self.write("\x1b[?1049l")  # leave alternate screen
        self.flush()

    def close(self) -> None:
        self._alive = False

    # -- resize ---------------------------------------------------------------

    def resize(self, width: int, height: int) -> None:
        self._size = (width, height)
        size = Size(width, height)
        self._post(events.Resize(size, size))

    # -- helpers --------------------------------------------------------------

    def _post(self, message: Any) -> None:
        import asyncio

        asyncio.run_coroutine_threadsafe(
            self._app._post_message(message), loop=self._loop
        )


def _bind_queue_driver(out_put: Any, holder: dict[str, Any]) -> type[QueueDriver]:
    """Return a QueueDriver subclass bound to one connection's outbound queue.

    Textual builds the driver itself, so we bake ``out_put`` into a subclass and
    stash the live instance in *holder* so the input pump can reach it.
    """

    class _BoundQueueDriver(QueueDriver):
        def __init__(self, app: Any, **kwargs: Any) -> None:
            self._out_put = out_put
            super().__init__(app, **kwargs)
            holder["driver"] = self

    return _BoundQueueDriver


def run_app(
    app_ref: str,
    in_queue: Any,
    out_queue: Any,
    cols: int,
    rows: int,
) -> None:
    """Subinterpreter entry point: build the app from *app_ref* and run it.

    *app_ref* is ``"package.module:attr"`` where ``attr`` is a zero-argument
    callable (typically an ``App`` subclass) returning a fresh app. It must be
    importable inside the subinterpreter (an installed package, or a module on
    ``PYTHONPATH``). Called via ``Interpreter.call_in_thread`` from the main
    interpreter.
    """
    import asyncio

    asyncio.run(_amain(app_ref, in_queue, out_queue, cols, rows))


async def _amain(
    app_ref: str,
    in_queue: Any,
    out_queue: Any,
    cols: int,
    rows: int,
) -> None:
    import asyncio
    import importlib
    import queue  # cross-interpreter Queue raises stdlib queue.Empty subclasses

    module_name, _, attr = app_ref.partition(":")
    factory = getattr(importlib.import_module(module_name), attr)
    app = factory()

    holder: dict[str, Any] = {}
    app.driver_class = _bind_queue_driver(out_queue.put, holder)
    stop = asyncio.Event()

    async def input_pump() -> None:
        # Wait until Textual has built the driver before consuming input, so
        # early keystrokes are not dropped.
        while holder.get("driver") is None and not stop.is_set():
            await asyncio.sleep(0.005)
        driver = holder.get("driver")
        while not stop.is_set():
            try:
                msg = in_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.005)
                continue
            tag = msg[0]
            if tag == "X":
                app.exit()
                break
            if driver is None:
                continue
            if tag == "D":
                driver.feed(msg[1])
            elif tag == "R":
                driver.resize(msg[1], msg[2])

    pump = asyncio.create_task(input_pump())
    try:
        await app.run_async(headless=False, mouse=True, size=(cols, rows))
        out_queue.put(("done",))
    except BaseException as exc:  # report any failure back to the main interp
        out_queue.put(("error", repr(exc)))
    finally:
        stop.set()
        pump.cancel()

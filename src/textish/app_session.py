import asyncio
import json
import logging
import os
from pathlib import Path

import asyncssh

from textish.types import ProcessState

from .protocol import encode_packet, read_packet

log = logging.getLogger("textish")

# Seconds to wait for the subprocess to exit cleanly before killing it.
_GRACEFUL_EXIT_TIMEOUT = 3.0


class AppSession:
    """Manages one SSH client's Textual app subprocess.

    Owns the full lifecycle of the subprocess: spawning it, performing the
    WebDriver handshake, forwarding packets in both directions, and tearing
    it down when the client disconnects.

    The session moves through states in :class:`~textish.types.ProcessState`
    in order: PENDING → RUNNING → STOPPING → STOPPED.
    """

    def __init__(
        self,
        app_command: str,
        channel: asyncssh.SSHServerChannel,
        cols: int = 80,
        rows: int = 24,
        working_dir: str | Path | None = None,
    ) -> None:
        """
        Args:
            app_command: Shell command to launch the Textual app.
            channel:     asyncssh channel used to write display data back to
                         the SSH client.
            cols:        Initial terminal width in columns.
            rows:        Initial terminal height in rows.
            working_dir: Working directory for the subprocess. Defaults to the
                         server's current directory.
        """
        self._app_command = app_command
        self._channel = channel
        self._cols = cols
        self._rows = rows
        self._working_dir = working_dir
        self._process: asyncio.subprocess.Process | None = None
        self._state = ProcessState.PENDING

    async def run(self) -> None:
        """Spawn the subprocess, complete the handshake, and relay packets.

        Sets ``TEXTUAL_DRIVER`` in the subprocess environment so Textual uses
        its WebDriver, which communicates over stdin/stdout using the packet
        protocol defined in :mod:`textish.protocol`.

        The handshake expects the WebDriver to print ``__GANGLION__`` on a
        line by itself before it starts sending packets. If that line never
        arrives (process crashed, wrong driver, etc.) the session is torn down.

        This coroutine runs until the subprocess exits or an error occurs.
        The SSH channel is always closed in the ``finally`` block, so the
        client is disconnected regardless of how the session ends.
        """
        env = {
            **os.environ,
            "TEXTUAL_DRIVER": "textual.drivers.web_driver:WebDriver",
            "COLUMNS": str(self._cols),
            "ROWS": str(self._rows),
        }
        self._process = await asyncio.create_subprocess_shell(
            self._app_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self._working_dir,
        )

        assert self._process is not None
        assert self._process.stdout is not None
        assert self._process.stderr is not None

        try:
            # The Textual WebDriver prints "__GANGLION__\n" before it starts
            # the packet protocol. We scan up to 10 lines to skip any startup
            # output before the marker arrives.
            ready = False
            for _ in range(10):
                line = await self._process.stdout.readline()
                if not line:
                    break
                if line == b"__GANGLION__\n":
                    ready = True
                    break

            if not ready:
                try:
                    stderr = (
                        await asyncio.wait_for(
                            self._process.stderr.read(4096), timeout=1.0
                        )
                        if self._process.stderr
                        else b""
                    )
                except Exception:
                    stderr = b"(could not read stderr)"
                log.error(
                    "WebDriver handshake failed — never received __GANGLION__\n"
                    "Subprocess stderr:\n%s",
                    stderr.decode(errors="replace"),
                )
                return

            self._state = ProcessState.RUNNING

            while True:
                result = await read_packet(self._process.stdout)
                if result is None:
                    break  # subprocess stdout closed (process exited)
                type_byte, payload = result
                if type_byte == b"D":
                    self._channel.write(payload)
                elif type_byte == b"M":
                    meta = json.loads(payload)
                    if meta.get("type") == "exit":
                        # App signalled a clean exit — give it time to flush
                        # before the finally block terminates it.
                        await asyncio.wait_for(
                            self._process.wait(), timeout=_GRACEFUL_EXIT_TIMEOUT
                        )

        finally:
            self._state = ProcessState.STOPPING
            self._channel.close()
            if self._process is not None and self._process.returncode is None:
                self._process.terminate()
                await self._process.wait()
            self._state = ProcessState.STOPPED

    async def _send_meta(self, payload: dict[str, object]) -> None:
        """Encode *payload* as JSON and write it as a meta (``b"M"``) packet.

        No-ops silently if the process or its stdin is no longer available.
        Callers are responsible for catching pipe errors if they care.
        """
        if self._process is None or self._process.stdin is None:
            return
        self._process.stdin.write(encode_packet(b"M", json.dumps(payload).encode()))
        await self._process.stdin.drain()

    async def send_input(self, data: bytes) -> None:
        """Forward raw input bytes from the SSH client to the app subprocess.

        Silently swallows broken-pipe errors — the subprocess may have already
        exited by the time this is called.
        """
        if self._process is None or self._process.stdin is None:
            return
        try:
            self._process.stdin.write(encode_packet(b"D", data))
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def resize(self, cols: int, rows: int) -> None:
        """Notify the app that the client terminal was resized.

        Sends a ``{"type": "resize", ...}`` meta packet. The Textual WebDriver
        uses this to trigger a layout reflow inside the app.
        """
        self._cols = cols
        self._rows = rows
        try:
            await self._send_meta({"type": "resize", "width": cols, "height": rows})
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def close(self) -> None:
        """Send a quit signal to the app and wait for the subprocess to exit.

        Idempotent — safe to call multiple times (only acts when RUNNING).
        Falls back to ``SIGKILL`` if the process does not exit within
        ``_GRACEFUL_EXIT_TIMEOUT`` seconds after receiving the quit signal.
        """
        if self._state is not ProcessState.RUNNING:
            return
        self._state = ProcessState.STOPPING

        if self._process is None:
            self._state = ProcessState.STOPPED
            return

        if self._process.stdin is not None:
            try:
                await self._send_meta({"type": "quit"})
                self._process.stdin.close()
            except Exception:
                log.debug("Error sending quit signal to subprocess.", exc_info=True)

        try:
            await asyncio.wait_for(self._process.wait(), timeout=_GRACEFUL_EXIT_TIMEOUT)
        except TimeoutError:
            log.warning("Subprocess did not exit after quit signal, killing.")
            self._process.kill()
            await self._process.wait()
        self._state = ProcessState.STOPPED

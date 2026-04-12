import asyncio
import json
import logging
import os
from pathlib import Path

from textish.types import ProcessState

from .protocol import encode_packet, read_packet

log = logging.getLogger("textish")

_GRACEFUL_EXIT_TIMEOUT = 3.0


class AppSession:
    """Manages a single user's session. Spawns the Textual app as a subprocess
    and bridges data between it and the SSH channel."""

    def __init__(
        self,
        app_command: str,
        channel,
        cols: int = 80,
        rows: int = 24,
        working_dir: str | Path | None = None,
    ) -> None:
        self._app_command = app_command
        self._channel = channel
        self._cols = cols
        self._rows = rows
        self._working_dir = working_dir
        self._process: asyncio.subprocess.Process | None = None
        self._state = ProcessState.PENDING

    async def run(self) -> None:
        """Spawn the subprocess and forward its output to the SSH channel."""
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

        try:
            # The Textual WebDriver prints "__GANGLION__\n" before starting the
            # packet protocol. Wait for it before forwarding data.
            ready = False
            for _ in range(10):
                line = await self._process.stdout.readline()
                if not line:
                    break
                if line == b"__GANGLION__\n":
                    ready = True
                    break

            if not ready:
                stderr = (
                    await self._process.stderr.read(4096)
                    if self._process.stderr
                    else b""
                )
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

    async def _send_meta(self, payload: dict) -> None:
        """Write a meta packet to the subprocess stdin and flush."""
        if self._process is None or self._process.stdin is None:
            return
        self._process.stdin.write(encode_packet(b"M", json.dumps(payload).encode()))
        await self._process.stdin.drain()

    async def send_input(self, data: bytes) -> None:
        """Forward a keypress from the SSH client to the app."""
        if self._process is None or self._process.stdin is None:
            return
        try:
            self._process.stdin.write(encode_packet(b"D", data))
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def resize(self, cols: int, rows: int) -> None:
        """Notify the app that the terminal was resized."""
        self._cols = cols
        self._rows = rows
        try:
            await self._send_meta({"type": "resize", "width": cols, "height": rows})
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def close(self) -> None:
        """Tell the app to quit and ensure the subprocess is killed."""
        if self._state is not ProcessState.RUNNING:
            return
        self._state = ProcessState.STOPPING

        if self._process is not None and self._process.stdin is not None:
            try:
                await self._send_meta({"type": "quit"})
                self._process.stdin.close()
            except Exception:
                log.debug("Error sending quit signal to subprocess.", exc_info=True)

        try:
            await asyncio.wait_for(
                self._process.wait(), timeout=_GRACEFUL_EXIT_TIMEOUT
            )
        except TimeoutError:
            log.warning("Subprocess did not exit after quit signal, killing.")
            self._process.kill()
            await self._process.wait()
        self._state = ProcessState.STOPPED

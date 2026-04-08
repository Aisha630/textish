import asyncio
import json
import logging
import os
from pathlib import Path

from .protocol import encode_packet, read_packet

log = logging.getLogger("textish")


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
                    await self._process.stderr.read() if self._process.stderr else b""
                )
                log.error(
                    "WebDriver handshake failed — never received __GANGLION__\n"
                    "Subprocess stderr:\n%s",
                    stderr.decode(errors="replace"),
                )
                return

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
                        pass  # keep reading until stdout closes so Textual can send cleanup sequences
        finally:
            # Terminate the subprocess forcibly if it's still running, and close the SSH channel
            self._channel.close()
            if self._process is not None and self._process.returncode is None:
                self._process.terminate()

    async def send_input(self, data: bytes) -> None:
        """Forward a keypress from the SSH client to the app."""
        if self._process is not None and self._process.stdin is not None:
            try:
                self._process.stdin.write(encode_packet(b"D", data))
                await self._process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass

    async def resize(self, cols: int, rows: int) -> None:
        """Notify the app that the terminal was resized."""
        self._cols = cols
        self._rows = rows
        if self._process is not None and self._process.stdin is not None:
            try:
                meta = json.dumps(
                    {"type": "resize", "width": cols, "height": rows}
                ).encode()
                self._process.stdin.write(encode_packet(b"M", meta))
                await self._process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass

    async def close(self) -> None:
        """Tell the app to quit and ensure the subprocess is killed."""
        if self._process is None:
            return

        if self._process.stdin is not None:
            try:
                meta = json.dumps({"type": "quit"}).encode()
                self._process.stdin.write(encode_packet(b"M", meta))
                await self._process.stdin.drain()
                self._process.stdin.close()
            except Exception:
                pass

        try:
            await asyncio.wait_for(self._process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            log.warning("Subprocess did not exit after quit signal, killing.")
            self._process.kill()
            await self._process.wait()

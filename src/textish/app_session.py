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
            env=env,
            cwd=self._working_dir,
        )

        try:
            # The textual WebDriver prints "__GANGLION__\n" when it's ready to receive packets, so wait for that before we start forwarding data. If we don't do this, we might send packets before the WebDriver is ready and they would be lost. Inspired by `textual-web`
            ready = False
            for _ in range(10):
                line = await self._process.stdout.readline()
                if not line:
                    break
                if line == b"__GANGLION__\n":
                    ready = True
                    break

            if not ready:
                log.error("WebDriver handshake failed — never received __GANGLION__")
                return

            while True:
                result = await read_packet(self._process.stdout)
                if result is None:
                    break  # subprocess stdout closed (process exited)
                type_byte, payload = result
                if type_byte == b"D":
                    # Raw terminal output — write directly to the user's terminal
                    self._channel.write(payload)
                elif type_byte == b"M":
                    meta = json.loads(payload)
                    if meta.get("type") == "exit":
                        break
        finally:
            self._channel.close()

    async def send_input(self, data: bytes) -> None:
        """Forward a keypress (or any stdin bytes) from the SSH client to the app."""
        if self._process is not None and self._process.stdin is not None:
            self._process.stdin.write(encode_packet(b"D", data))
            await self._process.stdin.drain()

    async def resize(self, cols: int, rows: int) -> None:
        """Notify the app that the terminal was resized."""
        self._cols = cols
        self._rows = rows
        if self._process is not None and self._process.stdin is not None:
            meta = json.dumps(
                {"type": "resize", "width": cols, "height": rows}
            ).encode()
            self._process.stdin.write(encode_packet(b"M", meta))
            await self._process.stdin.drain()

    async def close(self) -> None:
        """Tell the app to quit (called when the SSH client disconnects)."""
        if self._process is not None and self._process.stdin is not None:
            try:
                meta = json.dumps({"type": "quit"}).encode()
                self._process.stdin.write(encode_packet(b"M", meta))
                await self._process.stdin.drain()
            except Exception:
                pass  # channel may already be closing

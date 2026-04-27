import asyncio
import errno
import fcntl
import logging
import os
import pty
import struct
import termios
from collections.abc import Mapping
from pathlib import Path

import asyncssh

from textish.types import ProcessState

log = logging.getLogger("textish")

# Seconds to wait for the subprocess to exit cleanly before killing it.
_GRACEFUL_EXIT_TIMEOUT = 3.0


class AppSession:
    """Manages one SSH client's Textual app subprocess.

    Owns the full lifecycle of the subprocess: spawning it, performing the
    PTY setup, forwarding terminal bytes in both directions, and tearing it
    down when the client disconnects.

    The session moves through states in `textish.types.ProcessState`
    in order: PENDING → RUNNING → STOPPING → STOPPED.
    """

    def __init__(
        self,
        app_command: str,
        channel: asyncssh.SSHServerChannel[bytes],
        cols: int = 80,
        rows: int = 24,
        term_type: str = "xterm-256color",
        working_dir: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """
        Args:
            app_command: Shell command to launch the Textual app.
            channel:     asyncssh channel used to write display data back to
                         the SSH client.
            cols:        Initial terminal width in columns.
            rows:        Initial terminal height in rows.
            term_type:   Terminal type requested by the SSH client.
            working_dir: Working directory for the subprocess. Defaults to the
                         server's current directory.
            env:         Environment variables to pass to the subprocess.
        """
        self._app_command = app_command
        self._channel = channel
        self._cols = cols
        self._rows = rows
        self._term_type = term_type or "xterm-256color"
        self._working_dir = working_dir
        self._env = dict(env or {})
        self._process: asyncio.subprocess.Process | None = None
        self._master_fd: int | None = None
        self._state = ProcessState.PENDING

    async def run(self) -> None:
        """Spawn the subprocess under a PTY and relay terminal bytes.

        The subprocess is attached to the slave side of a pseudo-terminal, so
        Textual can use its normal terminal driver instead of the WebDriver.
        The master side is bridged to the SSH channel.

        This coroutine runs until the subprocess exits, the PTY closes, or an
        error occurs. The SSH channel is always closed in the ``finally`` block,
        so the client is disconnected regardless of how the session ends.
        """
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        os.set_blocking(master_fd, False)
        self._set_pty_size(self._cols, self._rows)

        env = self._build_subprocess_env()

        natural_exit = False
        try:
            try:
                self._process = await asyncio.create_subprocess_shell(
                    self._app_command,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=env,
                    cwd=self._working_dir,
                    preexec_fn=lambda: self._configure_child_pty(slave_fd),
                )
            finally:
                os.close(slave_fd)

            self._state = ProcessState.RUNNING

            while True:
                data = await self._read_pty()
                if data is None:
                    natural_exit = True
                    break
                self._channel.write(data)

        finally:
            self._state = ProcessState.STOPPING
            self._channel.close()
            if natural_exit:
                await self._wait_for_process_exit()
            else:
                await self._terminate_process()
            self._close_master_fd()
            self._state = ProcessState.STOPPED

    def _build_subprocess_env(self) -> dict[str, str]:
        """Build the environment for the app subprocess."""
        env = dict(self._env)
        env.update(
            {
                "COLUMNS": str(self._cols),
                "ROWS": str(self._rows),
                "TERM": self._term_type,
            }
        )
        return env

    async def _read_pty(self) -> bytes | None:
        """Read bytes from the PTY master, returning ``None`` on EOF."""
        if self._master_fd is None:
            return None

        while True:
            master_fd = self._master_fd
            if master_fd is None:
                return None
            try:
                data = os.read(master_fd, 65536)
                return data or None
            except BlockingIOError:
                pass
            except OSError as exc:
                # EIO is raised when the slave side of the PTY is closed,
                # which can happen when the subprocess exits. Treat it as EOF.
                # EBADF can occur if the master FD is closed concurrently.
                if exc.errno in (errno.EIO, errno.EBADF):
                    return None
                raise

            await self._wait_for_pty_readable(master_fd)

    async def _wait_for_pty_readable(self, master_fd: int) -> None:
        """Wait until the PTY master file descriptor has bytes to read."""
        loop = asyncio.get_running_loop()
        ready = loop.create_future()

        def _mark_ready(ready: asyncio.Future[None] = ready) -> None:
            # cannot set result on a future twice so guard against
            # accidental duplicate calls
            if not ready.done():
                ready.set_result(None)

        loop.add_reader(master_fd, _mark_ready)
        try:
            await ready
        finally:
            loop.remove_reader(master_fd)

    async def send_input(self, data: bytes) -> None:
        """Forward raw input bytes from the SSH client to the PTY.

        Silently swallows OS errors — the subprocess may have already
        exited by the time this is called.
        """
        if self._master_fd is None:
            return
        try:
            await self._write_pty(data)
        except OSError:
            pass

    async def _write_pty(self, data: bytes) -> None:
        """Write all of *data* to the PTY master."""

        # O(1) slicing
        view = memoryview(data)
        offset = 0

        while offset < len(view):
            master_fd = self._master_fd
            if master_fd is None:
                return
            try:
                written = os.write(master_fd, view[offset:])
            except BlockingIOError:
                await self._wait_for_pty_writable(master_fd)
                continue
            except InterruptedError:
                continue

            if written == 0:
                await self._wait_for_pty_writable(master_fd)
                continue
            offset += written

    async def _wait_for_pty_writable(self, master_fd: int) -> None:
        """Wait until the PTY master file descriptor can accept writes."""
        loop = asyncio.get_running_loop()
        ready = loop.create_future()

        def _mark_ready(ready: asyncio.Future[None] = ready) -> None:
            if not ready.done():
                ready.set_result(None)

        loop.add_writer(master_fd, _mark_ready)
        try:
            await ready
        finally:
            loop.remove_writer(master_fd)

    async def resize(self, cols: int, rows: int) -> None:
        """Notify the app that the client terminal was resized.

        Updates the PTY window size. Terminal apps receive the usual terminal
        resize signal from the operating system.
        """
        self._cols = cols
        self._rows = rows
        try:
            self._set_pty_size(cols, rows)
        except OSError:
            pass

    async def close(self) -> None:
        """Terminate the app and wait for the subprocess to exit.

        Idempotent — safe to call multiple times (only acts when RUNNING).
        Falls back to ``SIGKILL`` if the process does not exit within
        ``_GRACEFUL_EXIT_TIMEOUT`` seconds.
        """
        if self._state is not ProcessState.RUNNING:
            return
        self._state = ProcessState.STOPPING

        await self._terminate_process()
        self._close_master_fd()
        self._state = ProcessState.STOPPED

    def _set_pty_size(self, cols: int, rows: int) -> None:
        """Set PTY dimensions in rows/columns."""
        if self._master_fd is None:
            return
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, packed)

    @staticmethod
    def _configure_child_pty(slave_fd: int) -> None:
        """Make the PTY slave the child process's controlling terminal."""
        os.setsid()
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        except OSError:
            pass

    async def _terminate_process(self) -> None:
        """Terminate the subprocess, killing it if it does not exit promptly."""
        if self._process is None or self._process.returncode is not None:
            return

        try:
            self._process.terminate()
            await asyncio.wait_for(self._process.wait(), timeout=_GRACEFUL_EXIT_TIMEOUT)
        except TimeoutError:
            log.warning("Subprocess did not exit after terminate signal, killing.")
            self._process.kill()
            await self._process.wait()

    async def _wait_for_process_exit(self) -> None:
        """Reap a process which appears to have closed its PTY normally."""
        if self._process is None or self._process.returncode is not None:
            return

        try:
            await asyncio.wait_for(self._process.wait(), timeout=_GRACEFUL_EXIT_TIMEOUT)
        except TimeoutError:
            await self._terminate_process()

    def _close_master_fd(self) -> None:
        """Close the PTY master file descriptor once."""
        if self._master_fd is None:
            return
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        finally:
            self._master_fd = None

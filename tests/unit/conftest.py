import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from textish.app_session import AppSession
import textish.app_session as _app_session_module


@pytest.fixture
def mock_channel():
    """Mock asyncssh SSH channel."""
    return MagicMock()


@pytest.fixture
def mock_stdin():
    """Mock subprocess stdin."""
    stdin = MagicMock()
    stdin.drain = AsyncMock()
    return stdin


@pytest.fixture
def mock_ssh_conn():
    """Mock asyncssh SSH server connection."""
    conn = MagicMock()
    conn.get_extra_info.return_value = ("127.0.0.1", 12345)
    return conn

@pytest.fixture
def mock_session():
    """Creates an AppSession with a mocked subprocess and stdin"""
    
    session = AppSession("cmd", MagicMock())
    mock_stdin = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_process = MagicMock()
    mock_process.stdin = mock_stdin
    mock_process.wait = AsyncMock()
    session._process = mock_process
    
    return session

@pytest.fixture
def mock_process(monkeypatch):
    """Factory fixture for a mock subprocess.
    """
    def _factory(stdout_data=b"__GANGLION__\n", stderr_data=b"", returncode=0):
        stdout = asyncio.StreamReader()
        stdout.feed_data(stdout_data)
        stdout.feed_eof()

        stderr = asyncio.StreamReader()
        stderr.feed_data(stderr_data)
        stderr.feed_eof()

        proc = MagicMock()
        proc.stdout = stdout
        proc.stderr = stderr
        proc.returncode = returncode
        proc.wait = AsyncMock()

        async def _fake_create(*_, **__):
            return proc

        monkeypatch.setattr(_app_session_module.asyncio, "create_subprocess_shell", _fake_create)
        return proc

    return _factory
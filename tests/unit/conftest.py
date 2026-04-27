from unittest.mock import AsyncMock, MagicMock

import pytest

from textish.app_session import AppSession
from textish.server import SessionManager, TextishSSHServer
from textish.types import ProcessState


@pytest.fixture
def mock_channel():
    """Mock asyncssh SSH channel."""
    return MagicMock()


@pytest.fixture
def mock_ssh_conn():
    """Mock asyncssh SSH server connection."""
    conn = MagicMock()
    conn.get_extra_info.return_value = ("127.0.0.1", 12345)
    return conn


@pytest.fixture
def mock_session():
    """AppSession with a mocked subprocess in the RUNNING state."""
    session = AppSession("cmd", MagicMock())
    session._state = ProcessState.RUNNING
    process = MagicMock()
    process.returncode = None
    process.wait = AsyncMock()
    session._process = process
    return session


@pytest.fixture
def make_server():
    """Factory fixture for a TextishSSHServer with required args pre-filled."""

    def _factory(
        app_command="cmd",
        max_connections=0,
        auth_function=None,
        env=None,
    ):
        return TextishSSHServer(
            app_command,
            max_connections=max_connections,
            active_connections=set(),
            session_manager=SessionManager(),
            auth_function=auth_function,
            env=env,
        )

    return _factory

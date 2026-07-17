from unittest.mock import MagicMock

import pytest

from textish.server import SessionManager, TextishSSHServer


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
def make_server():
    """Factory fixture for a TextishSSHServer with required args pre-filled."""

    def _factory(
        app_ref="pkg.mod:App",
        max_connections=0,
        auth_function=None,
    ):
        return TextishSSHServer(
            app_ref,
            max_connections=max_connections,
            session_manager=SessionManager(),
            auth_function=auth_function,
        )

    return _factory

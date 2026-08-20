import asyncio
import stat
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from textish import _ensure_host_key, _run_server, _serve_async, serve, serve_async
from textish.config import AppConfig


def test_serve_accepts_local_factory_in_shared_interpreter():
    def factory():
        return object()

    with patch("textish._run_server") as run_server:
        serve(factory, log_level=None)

    config = run_server.call_args.args[0]
    assert config.app_factory is factory
    assert config.app_ref == ""


def test_serve_forwards_resource_limits():
    metrics_callback = MagicMock()
    with patch("textish._run_server") as run_server:
        serve(
            lambda: object(),
            workers=3,
            max_ssh_connections=20,
            max_authenticating=3,
            max_startups=2,
            max_pending_startups=6,
            login_timeout=12,
            backlog=256,
            channel_window=32 * 1024,
            output_buffer_limit=8192,
            max_terminal_width=100,
            max_terminal_height=50,
            metrics_interval=5,
            metrics_callback=metrics_callback,
            log_level=None,
        )

    config = run_server.call_args.args[0]
    assert config.workers == 3
    assert config.max_ssh_connections == 20
    assert config.max_authenticating == 3
    assert config.max_startups == 2
    assert config.max_pending_startups == 6
    assert config.login_timeout == 12
    assert config.backlog == 256
    assert config.channel_window == 32 * 1024
    assert config.output_buffer_limit == 8192
    assert (config.max_terminal_width, config.max_terminal_height) == (100, 50)
    assert config.metrics_interval == 5
    assert config.metrics_callback is metrics_callback


@pytest.mark.asyncio
async def test_serve_async_forwards_listener_limits(tmp_path):
    fake_server = MagicMock()
    fake_server.serve_forever = AsyncMock()
    fake_server.__aenter__ = AsyncMock(return_value=fake_server)
    fake_server.__aexit__ = AsyncMock(return_value=None)
    config = AppConfig(
        app_ref="pkg:App",
        host_key_path=str(tmp_path / "host_key"),
        backlog=321,
        login_timeout=17,
    )

    with patch(
        "asyncssh.create_server", new=AsyncMock(return_value=fake_server)
    ) as create:
        await serve_async(config)

    assert create.await_args.kwargs["backlog"] == 321
    assert create.await_args.kwargs["login_timeout"] == 17
    assert create.await_args.kwargs["reuse_port"] is False


@pytest.mark.asyncio
async def test_worker_listener_enables_port_reuse(tmp_path):
    fake_server = MagicMock()
    fake_server.serve_forever = AsyncMock()
    fake_server.__aenter__ = AsyncMock(return_value=fake_server)
    fake_server.__aexit__ = AsyncMock(return_value=None)
    config = AppConfig(app_ref="pkg:App", host_key_path=str(tmp_path / "host_key"))

    with patch(
        "asyncssh.create_server", new=AsyncMock(return_value=fake_server)
    ) as create:
        await _serve_async(config, reuse_port=True)

    assert create.await_args.kwargs["reuse_port"] is True


@pytest.mark.asyncio
async def test_serve_async_publishes_configured_metrics_callback(tmp_path):
    reported = asyncio.Event()
    snapshots = []

    async def metrics_callback(snapshot):
        snapshots.append(snapshot)
        reported.set()

    async def serve_until_reported():
        await asyncio.wait_for(reported.wait(), 1)

    fake_server = MagicMock()
    fake_server.serve_forever = AsyncMock(side_effect=serve_until_reported)
    fake_server.__aenter__ = AsyncMock(return_value=fake_server)
    fake_server.__aexit__ = AsyncMock(return_value=None)
    config = AppConfig(
        app_ref="pkg:App",
        host_key_path=str(tmp_path / "host_key"),
        metrics_interval=0.001,
        metrics_callback=metrics_callback,
    )

    with patch("asyncssh.create_server", new=AsyncMock(return_value=fake_server)):
        await serve_async(config)

    assert len(snapshots) == 1
    assert snapshots[0]["kind"] == "textish_metrics"


@pytest.mark.asyncio
async def test_serve_async_rejects_multiple_workers():
    with pytest.raises(ValueError, match="workers=1"):
        await serve_async(AppConfig(app_ref="pkg:App", workers=2))


def test_run_server_supervises_configured_workers(tmp_path):
    first = MagicMock()
    second = MagicMock()
    first.exitcode = 1
    second.exitcode = None
    context = MagicMock()
    context.Process.side_effect = [first, second]
    config = AppConfig(
        app_ref="pkg:App",
        workers=2,
        host_key_path=str(tmp_path / "host_key"),
    )

    with (
        patch("textish.mp.get_context", return_value=context) as get_context,
        patch("textish._stop_workers") as stop_workers,
        pytest.raises(RuntimeError, match="exited unexpectedly"),
    ):
        _run_server(config)

    get_context.assert_called_once_with("fork")
    first.start.assert_called_once()
    second.start.assert_called_once()
    worker_config = context.Process.call_args_list[0].kwargs["args"][0]
    assert worker_config.workers == 1
    assert worker_config.host_key_path == str(tmp_path / "host_key")
    stop_workers.assert_called_once_with([first, second])


def test_serve_rejects_non_callable_object():
    with pytest.raises(TypeError):
        serve(object(), log_level=None)


def test_app_config_defaults_to_localhost_and_allows_missing_key(tmp_path):
    config = AppConfig(app_ref="pkg.mod:App", host_key_path=str(tmp_path / "missing"))

    assert config.host == "127.0.0.1"
    assert config.host_key_path == str(tmp_path / "missing")


def test_app_config_accepts_direct_factory():
    def factory():
        return object()

    config = AppConfig(app_factory=factory)

    assert config.app_factory is factory


def test_app_config_rejects_ref_and_factory_together():
    with pytest.raises(ValueError, match="not both"):
        AppConfig(app_ref="pkg:App", app_factory=lambda: object())


@pytest.mark.parametrize("app_ref", ["", "module", ":App", "module:", "m:a:b"])
def test_app_config_rejects_malformed_reference(app_ref, tmp_path):
    with pytest.raises(ValueError, match="app_ref"):
        AppConfig(app_ref=app_ref, host_key_path=str(tmp_path / "key"))


def test_generated_host_key_has_private_permissions(tmp_path):
    path = tmp_path / "keys" / "host_key"

    result = _ensure_host_key(str(path))

    assert result == str(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_app_config_rejects_negative_idle_timeout():
    with pytest.raises(ValueError, match="idle_timeout"):
        AppConfig(app_ref="pkg:App", idle_timeout=-1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_ssh_connections", -1),
        ("workers", 0),
        ("max_authenticating", 0),
        ("max_startups", 0),
        ("login_timeout", -1),
        ("backlog", 0),
        ("channel_window", 1024),
        ("output_buffer_limit", 0),
        ("max_terminal_width", 0),
        ("max_terminal_height", 0),
        ("metrics_interval", -1),
    ],
)
def test_app_config_rejects_invalid_resource_limit(field, value):
    with pytest.raises(ValueError, match=field):
        AppConfig(app_ref="pkg:App", **{field: value})


def test_pending_startup_limit_must_cover_concurrent_startups():
    with pytest.raises(ValueError, match="max_pending_startups"):
        AppConfig(app_ref="pkg:App", max_startups=5, max_pending_startups=4)


def test_metrics_callback_requires_positive_interval():
    with pytest.raises(ValueError, match="metrics_interval"):
        AppConfig(app_ref="pkg:App", metrics_callback=lambda _snapshot: None)


def test_app_config_rejects_non_callable_metrics_callback():
    with pytest.raises(TypeError, match="metrics_callback"):
        AppConfig(
            app_ref="pkg:App",
            metrics_interval=1,
            metrics_callback="invalid",  # type: ignore[arg-type]
        )


def test_app_config_rejects_non_callable_auth():
    with pytest.raises(TypeError, match="auth"):
        AppConfig(app_ref="pkg:App", auth="invalid")  # type: ignore[arg-type]

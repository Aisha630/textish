import stat
from unittest.mock import patch

import pytest

from textish import _ensure_host_key, serve
from textish.config import AppConfig


def test_serve_accepts_local_factory_in_shared_interpreter():
    def factory():
        return object()

    with patch("textish._run_server") as run_server:
        serve(factory, log_level=None)

    config = run_server.call_args.args[0]
    assert config.app_factory is factory
    assert config.app_ref == ""


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


def test_app_config_rejects_non_callable_auth():
    with pytest.raises(TypeError, match="auth"):
        AppConfig(app_ref="pkg:App", auth="invalid")  # type: ignore[arg-type]

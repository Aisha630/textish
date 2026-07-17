import pytest

from textish import _resolve_app


class _FakeApp:
    """Stands in for a Textual App class defined in an importable module."""


# Give it a stable module path as if imported from a real module.
_FakeApp.__module__ = "my_pkg.my_mod"
_FakeApp.__qualname__ = "MyApp"


def test_resolve_app_from_class():
    assert _resolve_app(_FakeApp) == "my_pkg.my_mod:MyApp"


def test_resolve_app_from_string_passthrough():
    assert _resolve_app("pkg.mod:App") == "pkg.mod:App"


def test_resolve_app_from_factory():
    def make():
        return _FakeApp()

    make.__module__ = "my_pkg.my_mod"
    make.__qualname__ = "make"
    assert _resolve_app(make) == "my_pkg.my_mod:make"


def test_resolve_app_rejects_main_module():
    class Local:
        pass

    Local.__module__ = "__main__"
    Local.__qualname__ = "Local"
    with pytest.raises(ValueError, match="importable module"):
        _resolve_app(Local)


def test_resolve_app_rejects_non_app_object():
    with pytest.raises(TypeError):
        _resolve_app(object())

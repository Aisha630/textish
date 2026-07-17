"""Guard the subinterpreter worker's import chain.

Each connection runs ``textish.subinterp._worker.run_app`` inside a fresh
subinterpreter. Loading that function imports the ``textish`` package (its
``__init__``) and ``textish.subinterp`` (its ``__init__``). None of that may pull
in asyncssh, because asyncssh depends on cryptography, whose Rust bindings cannot
load in a subinterpreter (``ImportError: ... does not support loading in
subinterpreters``). These tests run on any Python version and fail loudly if the
worker import chain ever regains an asyncssh import.
"""

from __future__ import annotations

import os
import subprocess
import sys

import textish

# Directory that makes `textish` importable (the src/ dir in a checkout, or
# site-packages when installed), passed to the subprocess so it can import it.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(textish.__file__)))


def _modules_loaded_after_importing(module: str) -> set[str]:
    """Import *module* in a clean subprocess; return which heavy deps loaded."""
    code = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        "flagged = [m for m in ('asyncssh', 'cryptography') "
        "if m in sys.modules]\n"
        "print(','.join(flagged))\n"
    )
    pythonpath = _ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")
    env = {**os.environ, "PYTHONPATH": pythonpath}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return set(filter(None, result.stdout.strip().split(",")))


def test_importing_textish_does_not_import_asyncssh():
    loaded = _modules_loaded_after_importing("textish")
    assert loaded == set(), f"textish import pulled in {loaded} (must stay lazy)"


def test_importing_worker_does_not_import_asyncssh():
    loaded = _modules_loaded_after_importing("textish.subinterp._worker")
    assert loaded == set(), (
        f"worker import chain pulled in {loaded}; a subinterpreter cannot load "
        "cryptography's Rust bindings, so the worker must not import asyncssh"
    )


def test_importing_subinterp_package_does_not_import_asyncssh():
    loaded = _modules_loaded_after_importing("textish.subinterp")
    assert loaded == set(), f"textish.subinterp import pulled in {loaded}"

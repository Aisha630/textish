"""Tests for configurable / coloured logging setup."""

import logging

import pytest

from textish import _setup_logging


@pytest.fixture
def isolated_logger():
    """A fresh logger not shared with the root or pytest's capture handlers."""
    return logging.Logger("textish-test")


def test_none_level_installs_nothing(isolated_logger):
    _setup_logging(None, logger=isolated_logger)
    assert isolated_logger.handlers == []


def test_installs_handler_at_level(isolated_logger):
    _setup_logging("WARNING", color=False, logger=isolated_logger)
    assert len(isolated_logger.handlers) == 1
    assert isolated_logger.level == logging.WARNING


def test_respects_existing_configuration(isolated_logger):
    existing = logging.NullHandler()
    isolated_logger.addHandler(existing)
    _setup_logging("INFO", color=False, logger=isolated_logger)
    assert isolated_logger.handlers == [existing]


def test_plain_formatter_when_color_disabled(isolated_logger):
    _setup_logging("INFO", color=False, logger=isolated_logger)
    formatter = isolated_logger.handlers[0].formatter
    assert type(formatter) is logging.Formatter


def test_colorlog_formatter_when_available(isolated_logger):
    colorlog = pytest.importorskip("colorlog")
    _setup_logging("INFO", color=True, logger=isolated_logger)
    formatter = isolated_logger.handlers[0].formatter
    assert isinstance(formatter, colorlog.ColoredFormatter)

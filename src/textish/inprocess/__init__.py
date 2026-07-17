"""Shared-interpreter backend used by textish.

Every SSH session receives a fresh Textual ``App`` instance and driver, while
all sessions share the server's Python interpreter and asyncio event loop.
"""

from .session import InProcessAppSession

__all__ = ["InProcessAppSession"]

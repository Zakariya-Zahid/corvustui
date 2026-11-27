"""Textual user interface for CorvusTUI.

Textual is an optional dependency.  Importing this package without it raises
``ImportError``; the CLI catches that and prints an install hint.
"""

from corvustui.tui.app import CorvusTUIApp, TUIUnavailableError

__all__ = ["CorvusTUIApp", "TUIUnavailableError"]

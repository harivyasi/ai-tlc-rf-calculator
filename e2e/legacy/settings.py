"""Settings used only for regression testing against the original frontend.

Inherits all production settings but renders the *original* (pre-refactor)
``annotate.html`` — captured under ``e2e/legacy/templates/`` — through the
current backend. Because template ``DIRS`` are resolved before app template
dirs, ``/`` serves the legacy page while every view, URL, and security setting
stays identical to the refactored app.

This lets the Playwright suite run the same flow against the old and new
frontends and isolate exactly the JavaScript changes. It is not for production.
"""

from pathlib import Path

from config.settings import *  # noqa: F401,F403
from config.settings import TEMPLATES

TEMPLATES[0]["DIRS"] = [Path(__file__).resolve().parent / "templates"]

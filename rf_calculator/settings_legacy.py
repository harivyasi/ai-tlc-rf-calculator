"""Settings used only for regression testing against the original frontend.

Inherits all production settings but renders the *original* (pre-refactor)
``annotate.html`` — captured under ``regression_legacy_templates/`` — through the
current backend. Because template ``DIRS`` are resolved before app template
dirs, ``/`` serves the legacy page while every view, URL, and security setting
stays identical to the refactored app.

This lets the Playwright suite run the same flow against the old and new
frontends and isolate exactly the JavaScript changes. It is not for production.
"""

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR, TEMPLATES

TEMPLATES[0]["DIRS"] = [BASE_DIR / "regression_legacy_templates"]

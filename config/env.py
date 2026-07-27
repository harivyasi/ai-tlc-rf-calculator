"""Small helpers for reading configuration from environment variables."""

from __future__ import annotations

import os

_TRUTHY = {"true", "1", "t", "yes", "y", "on"}


def env_bool(name: str, default: bool = False) -> bool:
    """Return an environment variable interpreted as a boolean.

    Args:
        name: The environment variable name.
        default: Value to return when the variable is unset.

    Returns:
        ``True`` if the value is one of ``true/1/t/yes/y/on`` (case-insensitive).
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def env_int(name: str, default: int) -> int:
    """Return an environment variable parsed as an int, or ``default`` if unset/invalid.

    Args:
        name: The environment variable name.
        default: Value to return when the variable is unset or not an integer.

    Returns:
        The parsed integer, or ``default``.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_list(name: str, default: str = "") -> list[str]:
    """Return a comma-separated environment variable as a list of trimmed items.

    Args:
        name: The environment variable name.
        default: Fallback raw string used when the variable is unset.

    Returns:
        The non-empty, whitespace-stripped items.
    """
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]

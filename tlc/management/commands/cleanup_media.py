"""Management command that prunes old generated files from ``MEDIA_ROOT``.

The application writes annotated images, CSV exports, and downloaded ELN images
into ``MEDIA_ROOT`` and never removes them. Run this command (e.g. from cron) to
delete files older than a cutoff::

    python manage.py cleanup_media --hours 24
"""

from __future__ import annotations

import os
import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

# Files that must never be deleted regardless of age.
_PROTECTED = {".gitkeep"}


class Command(BaseCommand):
    help = "Delete generated media files older than the given age (default 24h)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--hours",
            type=float,
            default=24.0,
            help="Delete files older than this many hours (default: 24).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be deleted without removing anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        media_root = settings.MEDIA_ROOT
        if not os.path.isdir(media_root):
            self.stdout.write(f"MEDIA_ROOT does not exist: {media_root}")
            return

        cutoff = time.time() - options["hours"] * 3600
        dry_run: bool = options["dry_run"]
        removed = 0

        for entry in os.scandir(media_root):
            if not entry.is_file() or entry.name in _PROTECTED:
                continue
            if entry.stat().st_mtime >= cutoff:
                continue
            if dry_run:
                self.stdout.write(f"[dry-run] would delete {entry.name}")
            else:
                os.remove(entry.path)
                self.stdout.write(f"deleted {entry.name}")
            removed += 1

        verb = "would remove" if dry_run else "removed"
        self.stdout.write(self.style.SUCCESS(f"Done: {verb} {removed} file(s)."))

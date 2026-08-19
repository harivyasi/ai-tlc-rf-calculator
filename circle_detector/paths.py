"""Filesystem path primitives.

Reads and writes need different primitives, and conflating them is what
produced the traversal bugs this module replaces:

* a *read* takes a caller-supplied path and must prove it lands inside
  MEDIA_ROOT -- ``resolve_media_path``;
* a *write* takes a caller-supplied label and must not treat it as a path at
  all, so it is reduced to a bare stem that cannot contain a directory
  component -- ``safe_output_stem``.

Containment-checking a filename destined for a write is the wrong tool: it
rejects harmless ELN filenames while still leaving the caller in charge of
where the file lands.
"""
import io
import os
import re

from django.conf import settings

# Formats we are willing to store. Anything else -- including real image
# formats like GIF -- is refused rather than silently re-encoded.
_ALLOWED_FORMATS = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}

_DISALLOWED_IN_STEM = re.compile(r'[^A-Za-z0-9._-]')
_REPEATED_DOTS = re.compile(r'\.+')


def resolve_media_path(relative_path, must_exist=True):
    """Resolve a media-relative path, or return None if it escapes MEDIA_ROOT.

    Absolute paths are refused outright: every caller receives these values
    from a browser, and none of them has a legitimate reason to name a path
    outside the media directory.

    Symlinks are resolved before the containment check, so a link inside
    MEDIA_ROOT pointing outside it is refused too.
    """
    if not relative_path or not str(relative_path).strip():
        return None

    candidate = str(relative_path).strip()
    if os.path.isabs(candidate):
        return None

    media_root = os.path.realpath(str(settings.MEDIA_ROOT))
    full_path = os.path.realpath(os.path.join(media_root, candidate))

    # commonpath on the *resolved* paths; comparing the unresolved strings
    # would let both '..' segments and symlinks through.
    try:
        if os.path.commonpath([full_path, media_root]) != media_root:
            return None
    except ValueError:
        # Different drives on Windows, or a mix of absolute and relative.
        return None

    if must_exist and not os.path.isfile(full_path):
        return None
    return full_path


def safe_output_stem(value, fallback='image'):
    """Reduce an untrusted label to a filename stem safe to join onto a path.

    Guarantees the result contains no directory separator, no '..', and no
    characters outside [A-Za-z0-9._-]. Callers add their own extension and
    uniqueness token.
    """
    if not value:
        return fallback

    # Normalise Windows separators first so basename strips them too.
    name = os.path.basename(str(value).replace('\\', '/'))
    stem = os.path.splitext(name)[0]
    stem = _DISALLOWED_IN_STEM.sub('', stem)
    stem = _REPEATED_DOTS.sub('.', stem).strip('._-')

    if not stem:
        return fallback
    return stem[:64]


def safe_image_extension(raw_bytes, allowed=None):
    """Return the extension for image bytes, or None if they are not an image.

    The extension comes from the *decoded* format, never from a filename or a
    client-supplied Content-Type -- both are attacker-controlled, and trusting
    them is how an .html file ended up in the media directory.
    """
    if not raw_bytes:
        return None

    formats = allowed if allowed is not None else _ALLOWED_FORMATS
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw_bytes)) as image:
            image.verify()
            detected = image.format
    except Exception:
        return None

    if isinstance(formats, dict):
        return formats.get(detected)
    return _ALLOWED_FORMATS.get(detected) if detected in formats else None

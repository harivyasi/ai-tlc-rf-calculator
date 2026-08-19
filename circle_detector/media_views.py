"""Serving user-generated media.

The annotated image and the CSV are the app's primary output, and their URLs
used to be routed only when DEBUG was on -- so with DEBUG off they 404'd and
the feature was simply broken in production.

This wraps django.views.static.serve rather than reimplementing it: that view
already does containment via safe_join, plus conditional responses and
mimetype guessing. What is added here is an extension allowlist and headers
that keep anything which does reach the media directory from being rendered as
a page.
"""
import os

from django.http import Http404
from django.views.static import serve as static_serve

# Only formats this app itself produces or accepts. Anything else -- notably
# .html and .svg, both of which execute script in a browser -- is not served
# even if it somehow reaches MEDIA_ROOT.
SERVABLE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.csv'}

ATTACHMENT_EXTENSIONS = {'.csv'}


def serve_media(request, path):
    """Serve a file from MEDIA_ROOT, refusing anything not on the allowlist."""
    from django.conf import settings

    extension = os.path.splitext(path)[1].lower()
    if extension not in SERVABLE_EXTENSIONS:
        raise Http404('Not available.')

    response = static_serve(request, path, document_root=settings.MEDIA_ROOT)

    if extension in ATTACHMENT_EXTENSIONS:
        filename = os.path.basename(path)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # Defence in depth behind the upload-side format check: user-supplied
    # bytes live in this directory, so make it impossible for a response from
    # here to run as an active document.
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return response

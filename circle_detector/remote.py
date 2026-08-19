"""Guarded fetching of remote (ELN) images.

This endpoint is unauthenticated, so an unguarded fetch turns the app into a
proxy into whatever network it is deployed on. The controls here are, in order
of importance:

1. every redirect hop is revalidated -- validating only the first URL is the
   standard bypass, since a public host can redirect to 169.254.169.254;
2. the response body is capped as it streams, because Content-Length is
   attacker-controlled;
3. the stored extension comes from the decoded image, never from the URL or
   the Content-Type header.

Residual risk, accepted and documented: there is a DNS-rebinding window
between getaddrinfo() below and the connect() inside requests. Closing it
requires an HTTPAdapter that pins the validated address while preserving SNI
and the Host header. That is disproportionate here because setting
ELN_ALLOWED_HOSTS in production makes the window irrelevant -- allowlisting is
by name, and the attacker does not control the name.
"""
import ipaddress
import logging
import os
import socket
import time
import uuid
from urllib.parse import urljoin, urlsplit

import requests
from django.conf import settings

from .paths import safe_image_extension

logger = logging.getLogger(__name__)

DOWNLOAD_PREFIX = 'downloaded_'
_MAX_REDIRECTS = 3
_CHUNK = 64 * 1024


class RemoteFetchError(Exception):
    """A remote image could not be fetched safely. The message is safe to show
    to the caller; it never contains response bodies or internal paths."""


def _allowed_hosts():
    return [host.strip().lower() for host in getattr(settings, 'ELN_ALLOWED_HOSTS', []) if host.strip()]


def validate_remote_url(url):
    """Return the URL if it is safe to fetch, else raise RemoteFetchError."""
    if not url or not isinstance(url, str):
        raise RemoteFetchError('No image URL supplied.')

    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        raise RemoteFetchError('Only http and https URLs can be fetched.')
    if not parts.hostname:
        raise RemoteFetchError('The image URL has no host.')

    allowed = _allowed_hosts()
    if allowed:
        # Name-based allowlisting deliberately skips DNS: the operator has
        # named the hosts they trust, so resolution cannot change the verdict.
        if parts.hostname.lower() not in allowed:
            raise RemoteFetchError('That host is not in the configured ELN allowlist.')
        return url

    default_port = 443 if parts.scheme == 'https' else 80
    try:
        resolved = socket.getaddrinfo(parts.hostname, parts.port or default_port,
                                      proto=socket.IPPROTO_TCP)
    except (socket.gaierror, socket.error, ValueError) as exc:
        raise RemoteFetchError('The image host could not be resolved.') from exc

    for entry in resolved:
        address = entry[-1][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RemoteFetchError('The image host resolved to an unusable address.') from exc
        # is_global covers loopback, private, link-local (169.254.169.254),
        # CGNAT and reserved -- but NOT multicast: 224.0.0.1 and ff02::1 both
        # report is_global=True, so multicast is checked explicitly.
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or ip.is_loopback or ip.is_link_local or ip.is_unspecified):
            raise RemoteFetchError('That address range cannot be fetched.')

    return url


def fetch_remote_image(url):
    """Fetch a remote image safely. Returns (bytes, extension)."""
    max_bytes = int(getattr(settings, 'ELN_FETCH_MAX_BYTES', 25 * 1024 * 1024))
    timeout = getattr(settings, 'ELN_FETCH_TIMEOUT', (5, 20))
    response = None

    for _ in range(_MAX_REDIRECTS + 1):
        validate_remote_url(url)
        response = requests.get(url, stream=True, timeout=timeout, allow_redirects=False,
                                headers={'User-Agent': 'tlc-rf-calculator'})
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get('Location')
            response.close()
            if not location:
                raise RemoteFetchError('The image host returned an invalid redirect.')
            url = urljoin(url, location)
            continue
        break
    else:
        raise RemoteFetchError('Too many redirects while fetching the image.')

    if response.status_code != 200:
        response.close()
        raise RemoteFetchError('The image could not be retrieved from the ELN.')

    content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
    if not content_type.startswith('image/'):
        response.close()
        raise RemoteFetchError('That URL did not return an image.')

    body = bytearray()
    try:
        for chunk in response.iter_content(_CHUNK):
            if not chunk:
                continue
            body += chunk
            if len(body) > max_bytes:
                raise RemoteFetchError('The image is larger than the configured limit.')
    finally:
        response.close()

    extension = safe_image_extension(bytes(body))
    if extension is None:
        raise RemoteFetchError('The downloaded file is not a supported image.')

    return bytes(body), extension


def save_downloaded_image(raw_bytes, extension):
    """Store a fetched image flat in MEDIA_ROOT and return its relative name.

    Flat, not in a subdirectory: annotate.html reduces this value to its
    basename before posting it back as imageSrc, so a directory component
    would silently break calculate_rf and both auto-detect endpoints.
    """
    media_root = str(settings.MEDIA_ROOT)
    os.makedirs(media_root, exist_ok=True)

    name = f'{DOWNLOAD_PREFIX}{uuid.uuid4().hex[:8]}{extension}'
    with open(os.path.join(media_root, name), 'wb') as handle:
        handle.write(raw_bytes)
    return name


def sweep_old_downloads():
    """Delete fetched images past their retention window.

    Unique filenames fixed the overwrite race but removed the natural cap of
    one file, so an unauthenticated endpoint could otherwise fill the disk.
    """
    hours = getattr(settings, 'MEDIA_RETENTION_HOURS', 24)
    if not hours:
        return

    cutoff = time.time() - (float(hours) * 3600)
    try:
        entries = list(os.scandir(str(settings.MEDIA_ROOT)))
    except OSError:
        return

    for entry in entries:
        if not entry.name.startswith(DOWNLOAD_PREFIX) or not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
        except OSError:
            logger.warning('Could not remove expired download %s', entry.name)

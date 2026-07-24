"""Views for the TLC RF-calculator application.

The application lets a user annotate a Thin Layer Chromatography (TLC) plate
image in the browser and computes the retention factor (RF) for each marked
spot. Images arrive either from an Electronic Lab Notebook (ELN) via a remote
URL, or as a local upload whose bytes are sent base64-encoded in the request
body.

RF is defined as the distance travelled by a spot divided by the distance
travelled by the solvent front::

    rf = (spot_y - min_line_y) / (max_line_y - min_line_y)
"""

from __future__ import annotations

import base64
import binascii
import csv
import ipaddress
import json
import logging
import os
import socket
import uuid
from typing import Any
from urllib.parse import urlsplit

import cv2
import numpy as np
import requests
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from config.env import env_list

from .detection import detect_spots as _detect_spots

logger = logging.getLogger(__name__)

# Limits applied when fetching a remote (ELN) image.
FETCH_TIMEOUT_SECONDS = 10
MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25 MB


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness/readiness probe for container orchestration.

    Args:
        request: The incoming HTTP request.

    Returns:
        A small JSON body indicating the service is up.
    """
    return JsonResponse({"status": "ok"})


@ensure_csrf_cookie
def upload_image(request: HttpRequest) -> HttpResponse:
    """Render the single-page annotation tool.

    Decorated with :func:`ensure_csrf_cookie` so the ``csrftoken`` cookie is
    present for the JavaScript ``fetch`` call to ``calculate_rf``.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered annotation page.
    """
    return render(request, "tlc/annotate.html")


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


def _resolve_within_media(name: str) -> str | None:
    """Resolve a client-supplied name to an absolute path inside ``MEDIA_ROOT``.

    Only the final path component is used, so directory-traversal sequences
    (``..``) and absolute paths cannot escape the media directory.

    Args:
        name: The client-supplied file name.

    Returns:
        The absolute, canonicalised path, or ``None`` if it is empty or would
        resolve outside ``MEDIA_ROOT``.
    """
    safe_name = os.path.basename(name or "")
    if not safe_name or safe_name in (".", ".."):
        return None

    media_root = os.path.realpath(settings.MEDIA_ROOT)
    candidate = os.path.realpath(os.path.join(media_root, safe_name))
    if candidate == media_root or os.path.commonpath([media_root, candidate]) != media_root:
        return None
    return candidate


def _is_safe_remote_url(url: str) -> bool:
    """Return whether ``url`` is safe to fetch server-side (SSRF guard).

    The URL must use http(s) and must not resolve to a private, loopback,
    link-local, reserved, or otherwise non-public address. When the
    ``ELN_ALLOWED_HOSTS`` environment variable is set (comma-separated), the
    host must additionally appear in that allowlist.

    Args:
        url: The remote URL to validate.

    Returns:
        ``True`` if the URL is considered safe to fetch, else ``False``.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False

    allowed = [h.lower() for h in env_list("ELN_ALLOWED_HOSTS")]
    if allowed and parts.hostname.lower() not in allowed:
        return False

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        addr_infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False

    for info in addr_infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _load_annotation_image(
    data: dict[str, Any],
) -> tuple[np.ndarray | None, str, JsonResponse | None]:
    """Load the source image referenced by an annotation payload.

    Local uploads carry the image as a base64 ``imageData`` data URL; ELN
    images are referenced by ``imageSrc`` (a media file name).

    Args:
        data: The parsed JSON request body.

    Returns:
        A ``(image, base_name, error)`` tuple. On success ``error`` is ``None``;
        on failure ``image`` is ``None`` and ``error`` is a ready JsonResponse.
    """
    if data.get("isLocalImage") and data.get("imageData"):
        image = _decode_data_url(data["imageData"])
        if image is None:
            return (
                None,
                "",
                JsonResponse({"error": "Could not decode the uploaded image."}, status=400),
            )
        return image, os.path.basename(data.get("imageSrc") or "local_upload.png"), None

    image_src = data.get("imageSrc")
    if not image_src:
        return None, "", JsonResponse({"error": 'Missing "imageSrc" data in request'}, status=400)
    image_path = _resolve_within_media(image_src)
    if not image_path or not os.path.exists(image_path):
        logger.warning("Image not found for request: %r", image_src)
        return None, "", JsonResponse({"error": "Image not found."}, status=400)
    image = cv2.imread(image_path)
    if image is None:
        return None, "", JsonResponse({"error": "Failed to read the image file."}, status=400)
    return image, os.path.basename(image_src), None


def _decode_data_url(data_url: str) -> np.ndarray | None:
    """Decode a base64 ``data:`` URL into an OpenCV image (BGR).

    Args:
        data_url: A ``data:image/...;base64,<payload>`` string, or a bare
            base64 payload.

    Returns:
        The decoded image as a NumPy array, or ``None`` if decoding fails.
    """
    payload = data_url.split(",", 1)[1] if "," in data_url else data_url
    try:
        raw = base64.b64decode(payload, validate=True)
    except binascii.Error, ValueError:
        return None
    if len(raw) > MAX_IMAGE_BYTES:
        return None
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    return image


# ---------------------------------------------------------------------------
# RF calculation
# ---------------------------------------------------------------------------


def calculate_rf(request: HttpRequest) -> JsonResponse:
    """Compute RF values from annotation data and render an annotated image.

    Expects a JSON body containing ``lines`` (with ``min`` and ``max``
    reference lines plus optional ``vertical``/``horizontal`` label lines),
    ``spots``, and either ``imageSrc`` (a media file name, for ELN images) or
    ``imageData`` (a base64 data URL, for local uploads).

    Args:
        request: The HTTP request carrying the JSON annotation payload.

    Returns:
        A JSON response with ``rf_values``, ``annotated_image_url``, and
        ``csv_url``, or an ``error`` message with an appropriate status code.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=405)

    try:
        data: dict[str, Any] = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON format received")
        return JsonResponse({"error": "Invalid JSON format"}, status=400)

    lines = data.get("lines")
    spots = data.get("spots")
    if not lines:
        return JsonResponse({"error": 'Missing "lines" data in request'}, status=400)
    if not spots:
        return JsonResponse({"error": 'Missing "spots" data in request'}, status=400)

    min_line = next((line for line in lines if line.get("type") == "min"), None)
    max_line = next((line for line in lines if line.get("type") == "max"), None)
    if not min_line or not max_line:
        return JsonResponse({"error": 'Please add "min" and "max" lines.'}, status=400)

    min_y = min_line["y"]
    max_y = max_line["y"]
    if max_y == min_y:
        return JsonResponse({"error": '"min" and "max" lines must differ.'}, status=400)

    # Load the source image from the appropriate origin.
    image, base_name, error = _load_annotation_image(data)
    if error is not None:
        return error
    assert image is not None  # guaranteed when error is None

    rf_values: list[dict[str, Any]] = []
    for spot in spots:
        x = spot["x"]
        y = spot["y"]
        rf_value = round((y - min_y) / (max_y - min_y), 2)

        # Keep only physically meaningful values strictly between 0 and 1.
        if rf_value <= 0.0 or rf_value >= 1.0:
            continue

        closest_vertical = min(
            (line for line in lines if line.get("type") == "vertical"),
            key=lambda line: abs(line.get("x", 0) - x),
            default=None,
        )
        closest_horizontal = min(
            (line for line in lines if line.get("type") == "horizontal"),
            key=lambda line: abs(line.get("y", 0) - y),
            default=None,
        )
        solvent = closest_vertical["text"] if closest_vertical else "Unknown"
        compound = closest_horizontal["text"] if closest_horizontal else "Unknown"

        rf_values.append({"compound": compound, "solvent": solvent, "rf": rf_value})

        cv2.circle(image, (int(x), int(y)), 10, (0, 165, 255), -1)
        cv2.putText(
            image,
            f"RF={rf_value}",
            (int(x) + 5, int(y) - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 255),
            1,
        )
        cv2.putText(
            image,
            f"{compound}",
            (int(x) + 5, int(y) + 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 0, 0),
            1,
        )
        cv2.putText(
            image,
            f"{solvent}",
            (int(x) + 5, int(y) + 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 0, 0),
            1,
        )

    # Unique output names so concurrent requests never clobber each other.
    token = uuid.uuid4().hex
    stem = os.path.splitext(base_name)[0]
    annotated_name = f"annotated_{stem}_{token}.png"
    csv_name = f"rf_values_{stem}_{token}.csv"

    os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, annotated_name), image)

    with open(os.path.join(settings.MEDIA_ROOT, csv_name), "w", newline="") as csvfile:
        writer = csv.DictWriter(
            csvfile, fieldnames=["Solvent (X value)", "Compound (Y value)", "RF value"]
        )
        writer.writeheader()
        for value in rf_values:
            writer.writerow(
                {
                    "Solvent (X value)": value["solvent"],
                    "Compound (Y value)": value["compound"],
                    "RF value": value["rf"],
                }
            )

    return JsonResponse(
        {
            "rf_values": rf_values,
            "annotated_image_url": settings.MEDIA_URL + annotated_name,
            "csv_url": settings.MEDIA_URL + csv_name,
        }
    )


def detect_spots(request: HttpRequest) -> JsonResponse:
    """Detect candidate TLC spots and return them as editable suggestions.

    Accepts the same image payload as :func:`calculate_rf` (base64 ``imageData``
    for local uploads or ``imageSrc`` for ELN images) and returns spot centres
    in original-image coordinates, ready to drop into the annotation ``spots``
    array. Detection is best-effort — the user reviews the result.

    Args:
        request: The HTTP request carrying the JSON image payload.

    Returns:
        A JSON response with ``spots`` (a list of ``{"x", "y"}``) and ``count``.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=405)

    try:
        data: dict[str, Any] = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON format"}, status=400)

    image, _, error = _load_annotation_image(data)
    if error is not None:
        return error
    assert image is not None  # guaranteed when error is None

    spots = _detect_spots(image)
    return JsonResponse({"spots": spots, "count": len(spots)})


# ---------------------------------------------------------------------------
# Remote image fetching / serving
# ---------------------------------------------------------------------------


def fetch_and_save_image(request: HttpRequest) -> JsonResponse:
    """Fetch an ELN image from a remote URL and store it under ``MEDIA_ROOT``.

    The URL is validated against SSRF (see :func:`_is_safe_remote_url`), the
    download is bounded by a timeout and a maximum size, and the file is saved
    under a random name. Only that name (not a filesystem path) is returned, so
    the client cannot influence where subsequent reads occur.

    Args:
        request: The HTTP request; expects a ``url`` query parameter.

    Returns:
        A JSON response with ``imagePath`` (a media-relative file name), or an
        ``error`` message.
    """
    image_url = request.GET.get("url")
    if not image_url:
        return JsonResponse({"error": 'Missing "url" parameter'}, status=400)
    if not _is_safe_remote_url(image_url):
        logger.warning("Refused to fetch unsafe URL: %r", image_url)
        return JsonResponse({"error": "The provided URL is not allowed."}, status=400)

    try:
        response = requests.get(
            image_url, timeout=FETCH_TIMEOUT_SECONDS, stream=True, allow_redirects=False
        )
    except requests.RequestException:
        logger.warning("Error fetching remote image", exc_info=True)
        return JsonResponse({"error": "Failed to fetch image"}, status=400)

    if response.status_code != 200:
        return JsonResponse({"error": "Failed to fetch image"}, status=400)

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            return JsonResponse({"error": "Remote image is too large."}, status=400)
        chunks.append(chunk)
    content = b"".join(chunks)

    if cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR) is None:
        return JsonResponse({"error": "Remote resource is not a valid image."}, status=400)

    file_name = f"{uuid.uuid4().hex}.png"
    os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
    with open(os.path.join(settings.MEDIA_ROOT, file_name), "wb") as fh:
        fh.write(content)

    return JsonResponse({"imagePath": file_name})


def serve_image(request: HttpRequest) -> HttpResponse:
    """Serve an image stored under ``MEDIA_ROOT`` by name.

    The ``file_path`` parameter is confined to ``MEDIA_ROOT`` (see
    :func:`_resolve_within_media`), so arbitrary files on the host cannot be
    read.

    Args:
        request: The HTTP request; expects a ``file_path`` query parameter.

    Returns:
        The image bytes as ``image/png``, or a JSON ``error`` with status 404.
    """
    requested = request.GET.get("file_path")
    resolved = _resolve_within_media(requested or "")
    if not resolved or not os.path.exists(resolved):
        return JsonResponse({"error": "File not found"}, status=404)

    with open(resolved, "rb") as fh:
        return HttpResponse(fh.read(), content_type="image/png")

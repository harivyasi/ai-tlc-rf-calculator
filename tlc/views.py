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
import io
import ipaddress
import json
import logging
import os
import socket
import uuid
from typing import Any
from urllib.parse import urljoin, urlsplit

import cv2
import numpy as np
import requests
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from PIL import Image, ImageOps, UnidentifiedImageError

from config import __version__

from . import detection

logger = logging.getLogger(__name__)

# Limits applied when fetching a remote (ELN) image.
FETCH_TIMEOUT_SECONDS = 10
MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_REDIRECTS = 3  # ELN attachment URLs often redirect to storage
# Reject absurdly large images so a single request can't exhaust CPU/memory in
# the OpenCV paths (decoded to ~3 bytes/pixel).
MAX_IMAGE_PIXELS = 40_000_000  # 40 MP


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness/readiness probe for container orchestration.

    Args:
        request: The incoming HTTP request.

    Returns:
        A small JSON body indicating the service is up, with the app version.
    """
    return JsonResponse({"status": "ok", "version": __version__})


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
    return render(request, "tlc/annotate.html", {"version": __version__})


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
    link-local, reserved, or otherwise non-public address. When
    ``settings.ELN_ALLOWED_HOSTS`` is non-empty (set directly or derived from
    ``ELN_ORIGIN``), the host must additionally appear in that allowlist.

    Args:
        url: The remote URL to validate.

    Returns:
        ``True`` if the URL is considered safe to fetch, else ``False``.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False

    allowed = [h.lower() for h in settings.ELN_ALLOWED_HOSTS]
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


def _fetch_following_safe_redirects(url: str) -> requests.Response | None:
    """GET ``url``, following redirects while re-validating each hop for SSRF.

    The initial URL must already have passed :func:`_is_safe_remote_url`. Each
    redirect ``Location`` is resolved and re-validated before it is followed, so
    an ELN endpoint that redirects to storage works without opening an SSRF hole.

    Args:
        url: The (already validated) URL to fetch.

    Returns:
        The final non-redirect streamed response, or ``None`` if a redirect
        target is unsafe or missing, or the redirect limit is exceeded.
    """
    for _ in range(MAX_REDIRECTS + 1):
        response = requests.get(
            url, timeout=FETCH_TIMEOUT_SECONDS, stream=True, allow_redirects=False
        )
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("Location")
        response.close()
        if not location:
            return None
        url = urljoin(url, location)
        if not _is_safe_remote_url(url):
            logger.warning("Refused unsafe redirect target: %r", url)
            return None
    return None


def _rotate_image(image: np.ndarray, rotation: int) -> np.ndarray:
    """Rotate ``image`` clockwise by ``rotation`` degrees (0/90/180/270)."""
    if rotation == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if rotation == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


def _parse_rotation(raw: Any) -> int:
    """Return a valid clockwise rotation (0/90/180/270), else 0."""
    try:
        rotation = int(raw) % 360
    except TypeError, ValueError:
        return 0
    return rotation if rotation in (0, 90, 180, 270) else 0


def _load_annotation_image(
    data: dict[str, Any],
) -> tuple[np.ndarray | None, str, JsonResponse | None]:
    """Load the source image referenced by an annotation payload.

    Local uploads carry the image as a base64 ``imageData`` data URL; ELN
    images are referenced by ``imageSrc`` (a media file name). An optional
    ``rotation`` (clockwise degrees) is applied so the backend image matches the
    orientation the user annotated on.

    Args:
        data: The parsed JSON request body.

    Returns:
        A ``(image, base_name, error)`` tuple. On success ``error`` is ``None``;
        on failure ``image`` is ``None`` and ``error`` is a ready JsonResponse.
    """
    rotation = _parse_rotation(data.get("rotation"))

    if data.get("isLocalImage") and data.get("imageData"):
        image = _decode_data_url(data["imageData"])
        if image is None:
            return (
                None,
                "",
                JsonResponse({"error": "Could not decode the uploaded image."}, status=400),
            )
        base_name = os.path.basename(data.get("imageSrc") or "local_upload.png")
        return _rotate_image(image, rotation), base_name, _oversize_error(image)

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
    return _rotate_image(image, rotation), os.path.basename(image_src), _oversize_error(image)


def _decode_data_url(data_url: str) -> np.ndarray | None:
    """Decode a base64 ``data:`` URL into an OpenCV image (BGR).

    EXIF orientation is applied so the decoded image matches what the browser
    displayed on the canvas (browsers auto-orient; ``cv2.imdecode`` does not),
    keeping annotation coordinates aligned for photos taken on phones.

    Args:
        data_url: A ``data:image/...;base64,<payload>`` string, or a bare
            base64 payload.

    Returns:
        The decoded BGR image as a NumPy array, or ``None`` if decoding fails.
    """
    payload = data_url.split(",", 1)[1] if "," in data_url else data_url
    try:
        raw = base64.b64decode(payload, validate=True)
    except binascii.Error, ValueError:
        return None
    if len(raw) > MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as pil_image:
            oriented = ImageOps.exif_transpose(pil_image).convert("RGB")
    except UnidentifiedImageError, OSError, ValueError:
        return None
    rgb = np.asarray(oriented)
    return np.ascontiguousarray(rgb[:, :, ::-1])  # RGB -> BGR for OpenCV


def _oversize_error(image: np.ndarray) -> JsonResponse | None:
    """Return a 400 response if the image exceeds ``MAX_IMAGE_PIXELS``, else None."""
    height, width = image.shape[:2]
    if height * width > MAX_IMAGE_PIXELS:
        logger.warning("Refused oversized image: %dx%d", width, height)
        return JsonResponse({"error": "Image is too large to process."}, status=400)
    return None


# ---------------------------------------------------------------------------
# RF calculation
# ---------------------------------------------------------------------------

_RED = (0, 0, 255)
_BLUE = (255, 0, 0)
_ORANGE = (0, 165, 255)


def _parse_text_scale(raw: Any) -> float:
    """Return the annotation text-size multiplier, clamped to [0.1, 5.0].

    The frontend sends a fraction (e.g. ``1.0`` for 100%, ``2.5`` for 250%);
    anything missing or malformed falls back to 1.0. Clamped server-side so a
    crafted request cannot render absurdly large text.
    """
    try:
        scale = float(raw)
    except TypeError, ValueError:
        return 1.0
    return max(0.1, min(5.0, scale))


def _draw_annotations(
    image: np.ndarray, spots: list[dict[str, Any]], text_scale: float = 1.0
) -> None:
    """Draw the analysis results — spot rings and their labels — onto ``image``.

    Only the results are burned into the saved image: each spot is marked with a
    ring and annotated with its RF value, compound, and solvent. The transient
    reference lines / plate box used during analysis are deliberately not drawn.
    Marker/text sizes scale with the image so they stay legible on both small
    and multi-megapixel plates.

    Args:
        image: The BGR image to annotate (modified in place).
        spots: Spots to mark, each with ``x``, ``y``, ``rf``, ``compound``,
            ``solvent``.
        text_scale: User multiplier on the label text size (1.0 == 100%). Only
            the text (font, its stroke, and the label row spacing) scales; the
            ring markers keep their image-derived size.
    """
    text_scale = max(0.1, min(5.0, text_scale))
    height, width = image.shape[:2]
    s = max(1.0, min(height, width) / 1000.0)
    thickness = max(1, round(2 * s))
    radius = max(4, round(9 * s))
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.5 * s * text_scale
    text_thickness = max(1, round(2 * s * text_scale))
    line_gap = 20 * s * text_scale  # vertical spacing between label rows

    offset = round(6 * s)
    for spot in spots:
        x, y = int(spot["x"]), int(spot["y"])
        cv2.circle(image, (x, y), radius, _ORANGE, thickness)
        cv2.putText(
            image,
            f"RF={spot['rf']}",
            (x + offset, y - offset),
            font,
            font_scale,
            _RED,
            text_thickness,
        )
        # Draw the compound then the lane label below the spot, skipping any
        # blank label (an unmarked lane is simply omitted, not shown as text).
        labels = [text for text in (spot["compound"], spot["solvent"]) if text]
        for row, text in enumerate(labels, start=1):
            cv2.putText(
                image,
                text,
                (x + offset, y + round(row * line_gap)),
                font,
                font_scale,
                _BLUE,
                text_thickness,
            )


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
        # The lane label (vertical V-Line) is organisational and optional — if no
        # lane is marked it is left blank (not shown). The compound (horizontal
        # H-Line) is the spot's identity, which in TLC is often genuinely unknown,
        # so an unmarked compound defaults to "Unknown".
        solvent = (closest_vertical["text"].strip() if closest_vertical else "") or ""
        compound = (closest_horizontal["text"].strip() if closest_horizontal else "") or "Unknown"

        # x/y are echoed so the frontend can map each result row to its spot.
        rf_values.append({"compound": compound, "solvent": solvent, "rf": rf_value, "x": x, "y": y})

    # Burn only the results (spot rings + labels) onto the full-resolution
    # image — not the transient reference lines / plate box.
    _draw_annotations(image, rf_values, _parse_text_scale(data.get("textScale")))

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


def _parse_region(raw: Any) -> tuple[int, int, int, int] | None:
    """Parse an optional ``{x, y, width, height}`` region into a tuple, or None."""
    if not isinstance(raw, dict):
        return None
    try:
        x, y = int(raw["x"]), int(raw["y"])
        width, height = int(raw["width"]), int(raw["height"])
    except KeyError, TypeError, ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _parse_detection_request(
    request: HttpRequest,
) -> tuple[dict[str, Any], np.ndarray | None, JsonResponse | None]:
    """Shared front half of the detection views: method, JSON, image."""
    if request.method != "POST":
        return {}, None, JsonResponse({"error": "Invalid request"}, status=405)
    try:
        data: dict[str, Any] = json.loads(request.body)
    except json.JSONDecodeError:
        return {}, None, JsonResponse({"error": "Invalid JSON format"}, status=400)
    if not isinstance(data, dict):
        return {}, None, JsonResponse({"error": "Invalid JSON format"}, status=400)
    image, _, error = _load_annotation_image(data)
    return data, image, error


def detect_spots(request: HttpRequest) -> JsonResponse:
    """Propose candidate spots with TLC-SpotDet, inside the Min/Max band.

    Accepts the same image payload as :func:`calculate_rf` plus ``minY`` and
    ``maxY`` (the baseline and solvent front, original-image px), which are
    required: inference is restricted to the band between them (SI section 4).
    An optional ``region`` (``{x, y, width, height}``, the Plate Area) also
    limits the x-range. Candidates are suggestions the user reviews.

    Args:
        request: The HTTP request carrying the JSON payload.

    Returns:
        ``{"spots": [{"x", "y", "conf"}], "count", "roi", "model"}``, or an
        ``error``.
    """
    data, image, error = _parse_detection_request(request)
    if error is not None:
        return error
    assert image is not None  # guaranteed when error is None

    try:
        min_y, max_y = float(data["minY"]), float(data["maxY"])
    except KeyError, TypeError, ValueError:
        return JsonResponse(
            {"error": "Place the Min and Max lines before detecting spots."}, status=400
        )

    try:
        result = detection.detect_spots(
            image, min_y=min_y, max_y=max_y, region=_parse_region(data.get("region"))
        )
    except detection.DetectionError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("Spot detection failed")
        return JsonResponse({"error": "Spot detection failed."}, status=500)
    return JsonResponse({**result, "count": len(result["spots"])})


def detect_lines(request: HttpRequest) -> JsonResponse:
    """Propose the baseline (Min) and solvent front (Max) with TLC-LineDet.

    Accepts the same image payload as :func:`calculate_rf`. The two lines are
    assigned by position, not by class label (see :mod:`tlc.detection`).

    Args:
        request: The HTTP request carrying the JSON image payload.

    Returns:
        ``{"minY", "maxY", "estimated", "model"}`` in original-image px, or an
        ``error``. ``estimated`` names a line that was guessed because only
        the other one was detected.
    """
    _, image, error = _parse_detection_request(request)
    if error is not None:
        return error
    assert image is not None  # guaranteed when error is None

    try:
        result = detection.detect_lines(image)
    except detection.DetectionError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("Line detection failed")
        return JsonResponse({"error": "Line detection failed."}, status=500)
    return JsonResponse(result)


# ---------------------------------------------------------------------------
# Remote image fetching / serving
# ---------------------------------------------------------------------------


def fetch_and_save_image(request: HttpRequest) -> JsonResponse:
    """Fetch an ELN image from a remote URL and store it under ``MEDIA_ROOT``.

    The URL is validated against SSRF (see :func:`_is_safe_remote_url`);
    redirects are followed but each hop is re-validated
    (:func:`_fetch_following_safe_redirects`). The download is bounded by a
    timeout and a maximum size, and the file is saved under a random name. Only
    that name (not a filesystem path) is returned, so the client cannot
    influence where subsequent reads occur.

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
        response = _fetch_following_safe_redirects(image_url)
    except requests.RequestException:
        logger.warning("Error fetching remote image", exc_info=True)
        return JsonResponse({"error": "Failed to fetch image"}, status=400)

    if response is None or response.status_code != 200:
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

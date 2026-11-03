"""Assisted spot detection for TLC plate images.

Detection runs on a background-subtracted "darkness" map (bright where the plate
is locally darker than its surroundings — i.e. where UV-quenching spots and
pencil marks are), confined to the plate interior:

- ``blob`` — scikit-image Laplacian-of-Gaussian blob detection
  (:func:`skimage.feature.blob_log`).
- ``lane`` — locate sample lanes by a vertical intensity projection, then find
  2-D peaks (:func:`skimage.feature.peak_local_max`) within each lane. This
  models the column layout of a TLC plate.

Both are precision-limited on their own (a raw photo also contains the baseline,
edges, and lane labels), so candidates are gated by a **shape filter**: only
compact, spot-sized connected components survive. Lines (baseline, plate edges)
and thin label strokes are rejected. Results are conservative suggestions the
user reviews before calculating RF.
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy.signal import find_peaks
from skimage.feature import blob_log, peak_local_max

# Spot radius bounds (fraction of the smaller image dimension) and thresholds.
MIN_RADIUS_FRAC = 0.015
MAX_RADIUS_FRAC = 0.09
BLOB_THRESHOLD = 0.08
LANE_PROMINENCE = 0.20
PEAK_REL_THRESHOLD = 0.30
BINARY_THRESHOLD = 0.22  # darkness level that counts as "structure"
MAX_ASPECT = 2.5  # reject elongated components (lines)
PROCESS_MAX_DIM = 1200  # cap the working resolution for detection (speed)


def _plate_mask(image: np.ndarray, gray: np.ndarray, erode_px: int) -> np.ndarray:
    """Return a solid boolean mask of the plate interior.

    Prefers segmenting the fluorescent green plate (which cleanly excludes the
    surrounding background, the metal clip, and any lane labels printed on the
    white strip below the plate). Falls back to a brightness threshold when the
    image is not green (e.g. synthetic test images). The chosen region is filled
    (so interior spots are kept) and eroded by ``erode_px`` to drop the edges.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV) if image.ndim == 3 else None
    base: np.ndarray | None = None
    if hsv is not None:
        h, s, v = cv2.split(hsv)
        green = ((h >= 35) & (h <= 90) & (s >= 40) & (v >= 40)).astype(np.uint8) * 255
        if int(green.sum()) // 255 > 0.08 * gray.size:
            base = green
    if base is None:
        base = cv2.threshold(gray.astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    contours, _ = cv2.findContours(base, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return np.ones(gray.shape[:2], dtype=bool)
    mask: np.ndarray = np.zeros(gray.shape[:2], dtype=np.uint8)
    cv2.drawContours(mask, [max(contours, key=cv2.contourArea)], -1, 255, cv2.FILLED)
    if erode_px > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode_px + 1, 2 * erode_px + 1))
        mask = cv2.erode(mask, kernel)
    return mask > 0


def _darkness_map(image: np.ndarray, max_r: float) -> np.ndarray:
    """Return an illumination-flattened map (bright where spots are).

    Confined to the plate interior and normalised within it, so plate edges and
    off-plate clutter neither register nor dominate the normalisation.

    Args:
        image: BGR or grayscale image (already cropped to any region).
        max_r: Expected maximum spot radius (px); sizes the background kernel
            and the plate-edge erosion.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = gray.astype(np.float32)

    ksize = int(max_r * 1.5) | 1  # background scale, larger than a spot
    background = cv2.GaussianBlur(gray, (ksize, ksize), 0)
    darkness: np.ndarray = np.clip(background - gray, 0.0, None)
    darkness = cv2.GaussianBlur(darkness, (3, 3), 0)

    darkness[~_plate_mask(image, gray, erode_px=int(max_r))] = 0.0

    peak = float(darkness.max())
    if peak > 0:
        darkness = darkness / peak
    return darkness


def _candidate_points(darkness: np.ndarray, method: str, min_r: float, max_r: float) -> list:
    """Raw candidate (x, y) points from the requested detector(s)."""
    points: list[tuple[int, int]] = []
    if method in ("blob", "combined"):
        min_sigma = max(1.0, min_r / np.sqrt(2))
        max_sigma = max(min_sigma + 1.0, max_r / np.sqrt(2))
        blobs = blob_log(
            darkness,
            min_sigma=min_sigma,
            max_sigma=max_sigma,
            num_sigma=8,
            threshold=BLOB_THRESHOLD,
        )
        points += [(int(round(x)), int(round(y))) for y, x, _sigma in blobs]
    if method in ("lane", "combined"):
        points += _lane_peaks(darkness, min_r)
    return points


def _lane_peaks(darkness: np.ndarray, min_r: float) -> list[tuple[int, int]]:
    """Locate lanes by column projection, then 2-D peaks within each lane strip."""
    height, width = darkness.shape
    spacing = max(1, int(2 * min_r))
    column = darkness.sum(axis=0)
    if column.max() <= 0:
        return []
    column = cv2.GaussianBlur(column.reshape(1, -1), (spacing | 1, 1), 0).ravel()
    lane_xs, _ = find_peaks(column, distance=spacing, prominence=column.max() * LANE_PROMINENCE)

    half = max(2, int(1.5 * min_r))
    points: list[tuple[int, int]] = []
    for lane_x in lane_xs:
        x0 = max(0, lane_x - half)
        strip = darkness[:, x0 : min(width, lane_x + half)]
        if strip.size == 0 or strip.max() <= 0:
            continue
        for y, x_in_strip in peak_local_max(
            strip, min_distance=spacing, threshold_rel=PEAK_REL_THRESHOLD, exclude_border=False
        ):
            points.append((int(x0 + x_in_strip), int(y)))
    return points


def _shape_gated_spots(
    darkness: np.ndarray,
    candidates: list,
    min_r: float,
    max_r: float,
    ring_center: bool = False,
) -> list[tuple[int, int]]:
    """Keep candidates that fall on a compact, spot-sized component.

    Rejects lines (baseline, plate edges) and thin label strokes, and snaps each
    surviving spot to a centre. With ``ring_center`` the bounding-box centre is
    used (best for hollow / hand-drawn ring marks, robust to gaps and tails);
    otherwise the pixel centroid is used (best for filled spots).
    """
    binary: np.ndarray = (darkness > BINARY_THRESHOLD).astype(np.uint8)
    close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(3, int(min_r)),) * 2)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close)

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(binary)
    min_area = 0.3 * np.pi * min_r**2
    max_area = np.pi * (1.5 * max_r) ** 2

    spots: list[tuple[int, int]] = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if not (min_area <= area <= max_area):
            continue
        long_side, short_side = max(w, h), max(1, min(w, h))
        if long_side > 3 * max_r or long_side / short_side > MAX_ASPECT:
            continue
        if any(x <= px < x + w and y <= py < y + h for px, py in candidates):
            if ring_center:
                cx, cy = x + w / 2.0, y + h / 2.0
            else:
                cx, cy = centroids[i]
            spots.append((int(round(cx)), int(round(cy))))
    return spots


def _dedup(points: list, min_dist: float) -> list:
    """Drop points closer than ``min_dist`` to an already-kept point."""
    kept: list[tuple[int, int]] = []
    min_sq = min_dist * min_dist
    for x, y in points:
        if all((x - kx) ** 2 + (y - ky) ** 2 >= min_sq for kx, ky in kept):
            kept.append((x, y))
    return kept


def detect_spots(
    image: np.ndarray,
    *,
    method: str = "combined",
    region: tuple[int, int, int, int] | None = None,
    ring_center: bool = False,
) -> list[dict[str, int]]:
    """Detect candidate TLC spots.

    Args:
        image: BGR (or grayscale) image as a NumPy array.
        method: ``"blob"``, ``"lane"``, or ``"combined"`` (default; both).
        ring_center: Centre each spot on its bounding box rather than its pixel
            centroid — better for hollow / hand-drawn ring marks.
        region: Optional ``(x, y, width, height)`` in image coordinates to
            confine detection to (e.g. a user-drawn plate area). When given, the
            image is cropped to it and results are offset back to full-image
            coordinates; the automatic plate segmentation is thereby bypassed.

    Returns:
        Spot centres as ``{"x": int, "y": int}`` in original-image coordinates,
        sorted top-to-bottom then left-to-right.
    """
    if image is None or image.size == 0:
        return []

    # Work at a bounded resolution: LoG blob detection is very expensive at the
    # large sigmas a multi-megapixel image implies. Downscale for processing and
    # map results back. Radii stay tied to the full-image spot size (scaled by
    # the same factor), so detection behaves the same, just faster.
    orig_h, orig_w = image.shape[:2]
    factor = min(1.0, PROCESS_MAX_DIM / max(orig_h, orig_w))
    if factor < 1.0:
        image = cv2.resize(
            image, (round(orig_w * factor), round(orig_h * factor)), interpolation=cv2.INTER_AREA
        )

    scale = min(orig_h, orig_w) * factor
    min_r = max(2.0, MIN_RADIUS_FRAC * scale)
    max_r = max(min_r + 1.0, MAX_RADIUS_FRAC * scale)

    offset_x = offset_y = 0
    if region is not None:
        rx, ry, rw, rh = (round(v * factor) for v in region)
        x0, y0 = max(0, rx), max(0, ry)
        x1, y1 = min(image.shape[1], rx + rw), min(image.shape[0], ry + rh)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return []
        image = image[y0:y1, x0:x1]
        offset_x, offset_y = x0, y0

    darkness = _darkness_map(image, max_r)

    candidates = _candidate_points(darkness, method, min_r, max_r)
    spots = _shape_gated_spots(darkness, candidates, min_r, max_r, ring_center=ring_center)
    spots = _dedup(spots, min_dist=1.5 * min_r)

    # Map working-resolution coordinates back to the original image.
    inv = 1.0 / factor
    scaled = [(round((offset_x + x) * inv), round((offset_y + y) * inv)) for x, y in spots]
    scaled.sort(key=lambda p: (p[1], p[0]))
    return [{"x": x, "y": y} for x, y in scaled]

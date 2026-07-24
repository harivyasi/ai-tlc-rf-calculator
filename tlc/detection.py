"""Assisted spot detection for TLC plate images.

Uses an OpenCV blob detector to locate dark, roughly circular UV-quenching
spots on a bright plate. The results are deliberately conservative
(few false positives) because they are *suggestions*: the user reviews the
detected spots in the browser and adds or removes them before calculating RF.
"""

from __future__ import annotations

import cv2
import numpy as np


def detect_spots(
    image: np.ndarray,
    *,
    min_area_frac: float = 0.0005,
    max_area_frac: float = 0.05,
    min_circularity: float = 0.5,
    min_convexity: float = 0.6,
) -> list[dict[str, int]]:
    """Detect candidate TLC spots in an image.

    Args:
        image: BGR (or grayscale) image as a NumPy array.
        min_area_frac: Minimum blob area as a fraction of the image area.
        max_area_frac: Maximum blob area as a fraction of the image area.
        min_circularity: Minimum blob circularity (0-1).
        min_convexity: Minimum blob convexity (0-1).

    Returns:
        Spot centres as ``{"x": int, "y": int}`` in original-image
        coordinates, sorted top-to-bottom then left-to-right. Empty if none
        are found or the image is empty.
    """
    if image is None or image.size == 0:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape[:2]
    area = height * width

    params = cv2.SimpleBlobDetector_Params()  # type: ignore[attr-defined]
    params.filterByColor = True
    params.blobColor = 0  # dark blobs on a bright plate
    params.filterByArea = True
    params.minArea = max(1.0, min_area_frac * area)
    params.maxArea = max_area_frac * area
    params.filterByCircularity = True
    params.minCircularity = min_circularity
    params.filterByConvexity = True
    params.minConvexity = min_convexity
    params.filterByInertia = False

    detector = cv2.SimpleBlobDetector_create(params)  # type: ignore[attr-defined]
    keypoints = detector.detect(gray)

    spots = [{"x": int(round(kp.pt[0])), "y": int(round(kp.pt[1]))} for kp in keypoints]
    spots.sort(key=lambda s: (s["y"], s["x"]))
    return spots

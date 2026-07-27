"""Tests for the RF calculation and the security-sensitive image endpoints."""

from __future__ import annotations

import base64
import glob
import os

import cv2
import numpy as np
from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import reverse


def _png_data_url(width: int = 300, height: int = 300) -> str:
    """Return a base64 ``data:`` URL for a small solid PNG image."""
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    encoded = base64.b64encode(buffer.tobytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _valid_payload() -> dict:
    """A well-formed calculate_rf payload whose single spot yields RF 0.5."""
    return {
        "isLocalImage": True,
        "imageData": _png_data_url(),
        "imageSrc": "sample.png",
        "lines": [
            {"type": "min", "y": 200, "text": "min"},
            {"type": "max", "y": 0, "text": "max"},
        ],
        "spots": [{"x": 150, "y": 100}],  # midway -> RF 0.5
    }


class CalculateRfTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def tearDown(self) -> None:
        # Remove output files written into the real MEDIA_ROOT by the view.
        for pattern in ("annotated_sample_*.png", "rf_values_sample_*.csv"):
            for path in glob.glob(os.path.join(settings.MEDIA_ROOT, pattern)):
                os.remove(path)

    def test_rf_value_is_computed_correctly(self) -> None:
        response = self.client.post(
            reverse("calculate_rf"), data=_valid_payload(), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["rf_values"]), 1)
        self.assertEqual(body["rf_values"][0]["rf"], 0.5)

    def test_solvent_and_compound_come_from_nearest_lines(self) -> None:
        payload = {
            "isLocalImage": True,
            "imageData": _png_data_url(),
            "imageSrc": "sample.png",
            "lines": [
                {"type": "min", "y": 200, "text": "min"},
                {"type": "max", "y": 0, "text": "max"},
                {"type": "vertical", "x": 150, "text": "SolventB"},
                {"type": "horizontal", "y": 150, "text": "CompoundX"},
            ],
            "spots": [{"x": 150, "y": 100}],
        }
        response = self.client.post(
            reverse("calculate_rf"), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        value = response.json()["rf_values"][0]
        self.assertEqual(value["solvent"], "SolventB")
        self.assertEqual(value["compound"], "CompoundX")

    def test_missing_min_max_lines_is_rejected(self) -> None:
        payload = {
            "isLocalImage": True,
            "imageData": _png_data_url(),
            "lines": [{"type": "vertical", "x": 10, "text": "a"}],
            "spots": [{"x": 5, "y": 5}],
        }
        response = self.client.post(
            reverse("calculate_rf"), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)


class CsrfTests(SimpleTestCase):
    """Verify CSRF is enforced on calculate_rf and the token flow works.

    Django's default test client bypasses CSRF, so these use a client with
    ``enforce_csrf_checks=True`` to exercise the real browser behaviour.
    """

    def tearDown(self) -> None:
        for pattern in ("annotated_sample_*.png", "rf_values_sample_*.csv"):
            for path in glob.glob(os.path.join(settings.MEDIA_ROOT, pattern)):
                os.remove(path)

    def test_post_without_csrf_token_is_rejected(self) -> None:
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            reverse("calculate_rf"), data=_valid_payload(), content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)

    def test_post_with_csrf_token_is_accepted(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("upload_image"))  # @ensure_csrf_cookie sets the cookie
        token = client.cookies["csrftoken"].value
        response = client.post(
            reverse("calculate_rf"),
            data=_valid_payload(),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)


def _blobs_data_url(centers: list[tuple[int, int]], size: int = 500) -> str:
    """A light image with dark filled circles at ``centers`` (for detection)."""
    image = np.full((size, size, 3), 245, dtype=np.uint8)
    for cx, cy in centers:
        cv2.circle(image, (cx, cy), 20, (40, 40, 40), -1)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return "data:image/png;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")


class HealthzTests(SimpleTestCase):
    def test_healthz_returns_ok(self) -> None:
        response = Client().get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


class SecurityHeaderTests(SimpleTestCase):
    def test_csp_header_locks_scripts_without_unsafe_inline(self) -> None:
        response = Client().get(reverse("upload_image"))
        csp = response.headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)


class DetectSpotsTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_detects_expected_number_of_spots(self) -> None:
        centers = [(120, 120), (360, 130), (240, 360)]
        payload = {"isLocalImage": True, "imageData": _blobs_data_url(centers)}
        response = self.client.post(
            reverse("detect_spots"), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], len(centers))
        # Each detected centre should lie near a drawn blob.
        for spot in body["spots"]:
            self.assertTrue(
                any(abs(spot["x"] - cx) < 15 and abs(spot["y"] - cy) < 15 for cx, cy in centers),
                f"unexpected spot {spot}",
            )

    def test_blank_image_yields_no_spots(self) -> None:
        payload = {"isLocalImage": True, "imageData": _blobs_data_url([])}
        response = self.client.post(
            reverse("detect_spots"), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)


class ServeImageSecurityTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_path_traversal_is_rejected(self) -> None:
        response = self.client.get(reverse("serve_image"), {"file_path": "../../../../etc/passwd"})
        self.assertEqual(response.status_code, 404)

    def test_legitimate_media_file_is_served(self) -> None:
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        name = "unit_test_image.png"
        cv2.imwrite(os.path.join(settings.MEDIA_ROOT, name), np.zeros((4, 4, 3), np.uint8))
        try:
            response = self.client.get(reverse("serve_image"), {"file_path": name})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "image/png")
        finally:
            os.remove(os.path.join(settings.MEDIA_ROOT, name))


class FetchImageSecurityTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_missing_url_is_rejected(self) -> None:
        response = self.client.get(reverse("fetch_image"))
        self.assertEqual(response.status_code, 400)

    def test_loopback_url_is_rejected(self) -> None:
        response = self.client.get(reverse("fetch_image"), {"url": "http://127.0.0.1/x.png"})
        self.assertEqual(response.status_code, 400)

    def test_non_http_scheme_is_rejected(self) -> None:
        response = self.client.get(reverse("fetch_image"), {"url": "file:///etc/passwd"})
        self.assertEqual(response.status_code, 400)

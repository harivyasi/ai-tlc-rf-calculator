"""Tests for the RF calculation and the security-sensitive image endpoints."""

from __future__ import annotations

import base64
import glob
import io
import os
import shutil
import socket
import tempfile
import time
from typing import Any
from unittest import mock

import cv2
import numpy as np
import requests
from django.conf import settings
from django.core.management import call_command
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse
from PIL import Image

from config.env import csrf_origins_from_hosts, env_bool, env_int, env_list, host_of
from tlc.detection import detect_spots
from tlc.views import (
    _decode_data_url,
    _draw_annotations,
    _fetch_following_safe_redirects,
    _is_safe_remote_url,
    _parse_region,
    _parse_text_scale,
    _resolve_within_media,
)


def _fake_response(status: int, headers: dict | None = None, body: bytes = b"") -> mock.Mock:
    """Build a minimal stand-in for a streamed ``requests.Response``."""
    response = mock.Mock()
    response.status_code = status
    response.headers = headers or {}
    response.iter_content = lambda chunk_size=65536: iter([body] if body else [])
    response.close = lambda: None
    return response


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

    def test_unmarked_labels_default_lane_blank_compound_unknown(self) -> None:
        # No V-Line / H-Line: the lane label is blank (omitted) while the
        # compound defaults to "Unknown".
        response = self.client.post(
            reverse("calculate_rf"), data=_valid_payload(), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        value = response.json()["rf_values"][0]
        self.assertEqual(value["solvent"], "")
        self.assertEqual(value["compound"], "Unknown")

    def test_large_local_upload_is_accepted(self) -> None:
        # A multi-MB image (base64 body > Django's 2.5 MB default) must not be
        # rejected as RequestDataTooBig.
        big = np.random.randint(0, 255, (1200, 1200, 3), dtype=np.uint8)
        ok, buffer = cv2.imencode(".png", big)
        assert ok and len(buffer) > 2_621_440
        payload = {
            "isLocalImage": True,
            "imageData": "data:image/png;base64," + base64.b64encode(buffer.tobytes()).decode(),
            "imageSrc": "sample.png",
            "lines": [{"type": "min", "y": 1000}, {"type": "max", "y": 100}],
            "spots": [{"x": 600, "y": 550}],
        }
        response = self.client.post(
            reverse("calculate_rf"), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

    def test_saved_image_has_rings_but_not_reference_lines(self) -> None:
        # Full-resolution output that saves the spot rings/labels but NOT the
        # transient reference lines used during analysis.
        payload = {
            "isLocalImage": True,
            "imageData": _png_data_url(width=400, height=600),
            "imageSrc": "sample.png",
            "lines": [
                {"type": "min", "y": 500, "text": "min"},
                {"type": "max", "y": 100, "text": "max"},
            ],
            "spots": [{"x": 200, "y": 300}],
        }
        response = self.client.post(
            reverse("calculate_rf"), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        name = response.json()["annotated_image_url"].split("/")[-1]
        annotated = cv2.imread(os.path.join(settings.MEDIA_ROOT, name))
        assert annotated is not None
        self.assertEqual(annotated.shape[:2], (600, 400))  # full input resolution
        # The spot ring (orange) is drawn.
        self.assertTrue(np.all(annotated == (0, 165, 255), axis=2).any(), "spot ring not drawn")
        # The max reference line (would span row y=100 in red) is NOT saved.
        red_line = np.all(annotated[100] == (0, 0, 255), axis=1)
        self.assertFalse(red_line.any(), "reference lines must not be saved")

    def test_rotation_is_applied_to_output(self) -> None:
        # A 400x600 image rotated 90° must produce a 600x400 annotated output.
        payload = {
            "isLocalImage": True,
            "imageData": _png_data_url(width=400, height=600),
            "imageSrc": "sample.png",
            "rotation": 90,
            "lines": [{"type": "min", "y": 350}, {"type": "max", "y": 50}],
            "spots": [{"x": 300, "y": 200}],  # in rotated (600x400) space -> RF 0.5
        }
        response = self.client.post(
            reverse("calculate_rf"), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["rf_values"][0]["rf"], 0.5)
        name = body["annotated_image_url"].split("/")[-1]
        annotated = cv2.imread(os.path.join(settings.MEDIA_ROOT, name))
        assert annotated is not None
        self.assertEqual(annotated.shape[:2], (400, 600))  # rotated dimensions

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
    def test_healthz_returns_ok_with_version(self) -> None:
        from config import __version__

        response = Client().get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["version"], __version__)


class VersionTests(SimpleTestCase):
    def test_pyproject_and_package_versions_match(self) -> None:
        import tomllib
        from pathlib import Path

        from config import __version__

        pyproject = Path(settings.BASE_DIR) / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        self.assertEqual(data["project"]["version"], __version__)


class SecurityHeaderTests(SimpleTestCase):
    def test_csp_header_locks_scripts_without_unsafe_inline(self) -> None:
        response = Client().get(reverse("upload_image"))
        csp = response.headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
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

    def test_ring_center_beats_centroid_on_open_ring(self) -> None:
        # An open/gappy ring: its pixel centroid is pulled toward the arc mass,
        # while the bounding-box centre (ring_center) stays near the true centre.
        img = np.full((500, 500, 3), 245, np.uint8)
        cv2.ellipse(img, (250, 250), (60, 60), 0, -60, 240, (40, 40, 40), 4)

        centroid = detect_spots(img, ring_center=False)
        ring = detect_spots(img, ring_center=True)
        self.assertEqual(len(centroid), 1)
        self.assertEqual(len(ring), 1)

        def dist(spots: list) -> float:
            return ((spots[0]["x"] - 250) ** 2 + (spots[0]["y"] - 250) ** 2) ** 0.5

        self.assertLess(dist(ring), dist(centroid))

    def test_region_confines_detection(self) -> None:
        # Three blobs; a region covering only the top two must yield two spots.
        centers = [(120, 120), (360, 130), (240, 360)]
        payload = {
            "isLocalImage": True,
            "imageData": _blobs_data_url(centers),
            "region": {"x": 0, "y": 0, "width": 500, "height": 280},
        }
        response = self.client.post(
            reverse("detect_spots"), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertTrue(all(spot["y"] < 280 for spot in body["spots"]))


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

    def test_redirect_to_private_ip_is_rejected(self) -> None:
        # A public URL that redirects to a private address must not be followed.
        with mock.patch("tlc.views.requests.get") as mock_get:
            mock_get.return_value = _fake_response(302, {"Location": "http://10.0.0.1/x.png"})
            response = self.client.get(reverse("fetch_image"), {"url": "http://93.184.216.34/a"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(mock_get.call_count, 1)  # private target never fetched

    def test_public_redirect_is_followed(self) -> None:
        ok, buf = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))
        assert ok
        with mock.patch("tlc.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _fake_response(302, {"Location": "http://93.184.216.34/final.png"}),
                _fake_response(200, body=buf.tobytes()),
            ]
            response = self.client.get(reverse("fetch_image"), {"url": "http://93.184.216.34/a"})
        self.assertEqual(response.status_code, 200)
        name = response.json()["imagePath"]
        os.remove(os.path.join(settings.MEDIA_ROOT, name))


class DecodeExifTests(SimpleTestCase):
    def test_exif_orientation_is_applied(self) -> None:
        # A 100x50 image tagged orientation=6 (rotate 90° CW on display) must
        # decode to 50 wide x 100 tall, matching what the browser shows.
        image = Image.new("RGB", (100, 50), (200, 30, 30))
        exif = image.getexif()
        exif[274] = 6  # EXIF Orientation tag
        buf = io.BytesIO()
        image.save(buf, format="JPEG", exif=exif)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

        decoded = _decode_data_url(data_url)
        assert decoded is not None
        self.assertEqual(decoded.shape[0], 100)  # height
        self.assertEqual(decoded.shape[1], 50)  # width

    def test_invalid_payload_returns_none(self) -> None:
        self.assertIsNone(_decode_data_url("data:image/png;base64,not-valid-base64!!"))


class CleanupMediaCommandTests(SimpleTestCase):
    def test_removes_old_keeps_new_and_gitkeep(self) -> None:
        tmp = tempfile.mkdtemp()
        try:
            old = os.path.join(tmp, "annotated_old.png")
            new = os.path.join(tmp, "annotated_new.png")
            keep = os.path.join(tmp, ".gitkeep")
            for path in (old, new, keep):
                open(path, "w").close()
            stale = time.time() - 3 * 3600
            os.utime(old, (stale, stale))
            os.utime(keep, (stale, stale))  # protected despite being old

            with self.settings(MEDIA_ROOT=tmp):
                call_command("cleanup_media", "--hours", "1")

            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(new))
            self.assertTrue(os.path.exists(keep))
        finally:
            shutil.rmtree(tmp)

    def test_dry_run_deletes_nothing(self) -> None:
        tmp = tempfile.mkdtemp()
        try:
            old = os.path.join(tmp, "annotated_old.png")
            open(old, "w").close()
            stale = time.time() - 3 * 3600
            os.utime(old, (stale, stale))
            with self.settings(MEDIA_ROOT=tmp):
                call_command("cleanup_media", "--hours", "1", "--dry-run")
            self.assertTrue(os.path.exists(old))
        finally:
            shutil.rmtree(tmp)


class EnvHelperTests(SimpleTestCase):
    def test_env_bool(self) -> None:
        with mock.patch.dict(os.environ, {"TLC_X": "yes"}):
            self.assertTrue(env_bool("TLC_X"))
        with mock.patch.dict(os.environ, {"TLC_X": "0"}):
            self.assertFalse(env_bool("TLC_X"))
        self.assertTrue(env_bool("TLC_MISSING", True))

    def test_env_int(self) -> None:
        with mock.patch.dict(os.environ, {"TLC_N": "42"}):
            self.assertEqual(env_int("TLC_N", 1), 42)
        with mock.patch.dict(os.environ, {"TLC_N": "nope"}):
            self.assertEqual(env_int("TLC_N", 7), 7)
        self.assertEqual(env_int("TLC_MISSING", 5), 5)

    def test_env_list(self) -> None:
        with mock.patch.dict(os.environ, {"TLC_L": "a, b ,,c"}):
            self.assertEqual(env_list("TLC_L"), ["a", "b", "c"])
        self.assertEqual(env_list("TLC_MISSING"), [])

    def test_csrf_origins_from_hosts(self) -> None:
        # Real hosts -> https origins; local/wildcard hosts dropped.
        self.assertEqual(
            csrf_origins_from_hosts(["tlc.example.org", "localhost", "127.0.0.1", "*.foo"]),
            ["https://tlc.example.org"],
        )
        self.assertEqual(csrf_origins_from_hosts(["localhost", "127.0.0.1"]), [])

    def test_host_of(self) -> None:
        self.assertEqual(host_of("https://eln.example.org/api/v1"), "eln.example.org")
        self.assertIsNone(host_of(""))


class SettingsDerivationTests(SimpleTestCase):
    """A single ELN_ORIGIN drives both the CSP connect-src and the SSRF allowlist."""

    def test_eln_origin_populates_csp_and_allowlist(self) -> None:
        import subprocess
        import sys

        code = (
            "import django; django.setup(); from django.conf import settings; "
            "print('HOSTS', settings.ELN_ALLOWED_HOSTS); "
            "print('CSP', 'https://eln.example.org' in settings.CONTENT_SECURITY_POLICY); "
            "print('CSRF', settings.CSRF_TRUSTED_ORIGINS)"
        )
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DEBUG": "False",
            "SECRET_KEY": "x" * 50,
            "ELN_ORIGIN": "https://eln.example.org",
            "ALLOWED_HOSTS": "tlc.example.org",
        }
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
        ).stdout
        self.assertIn("HOSTS ['eln.example.org']", out)  # allowlist derived from ELN_ORIGIN
        self.assertIn("CSP True", out)  # connect-src includes the ELN origin
        self.assertIn("CSRF ['https://tlc.example.org']", out)  # derived from ALLOWED_HOSTS


class TextScaleTests(SimpleTestCase):
    """Annotation text-size multiplier: parsing/clamping and its effect."""

    def test_parse_text_scale_clamps_and_defaults(self) -> None:
        self.assertEqual(_parse_text_scale(None), 1.0)  # missing -> 100%
        self.assertEqual(_parse_text_scale("not a number"), 1.0)  # malformed -> 100%
        self.assertEqual(_parse_text_scale(2.5), 2.5)  # in range, passes through
        self.assertEqual(_parse_text_scale(100), 5.0)  # above cap -> clamped to 500%
        self.assertEqual(_parse_text_scale(-3), 0.1)  # below floor -> clamped to 10%

    def test_larger_text_scale_draws_more_label_pixels(self) -> None:
        spot = {"x": 200, "y": 200, "rf": 0.5, "compound": "Aspirin", "solvent": "EtOAc"}

        def red_label_pixels(scale: float) -> int:
            img = np.full((400, 400, 3), 255, dtype=np.uint8)
            _draw_annotations(img, [dict(spot)], text_scale=scale)
            # _RED (0, 0, 255) is used only for the "RF=" label text.
            return int(np.all(img == (0, 0, 255), axis=2).sum())

        self.assertGreater(red_label_pixels(3.0), red_label_pixels(1.0))


class CalculateRfValidationTests(SimpleTestCase):
    """Request-validation and image-loading error branches of calculate_rf."""

    def setUp(self) -> None:
        self.client = Client()

    def tearDown(self) -> None:
        for pattern in ("annotated_*.png", "rf_values_*.csv"):
            for path in glob.glob(os.path.join(settings.MEDIA_ROOT, pattern)):
                os.remove(path)

    def _post(self, payload: dict) -> Any:
        return self.client.post(
            reverse("calculate_rf"), data=payload, content_type="application/json"
        )

    def test_get_is_rejected(self) -> None:
        self.assertEqual(self.client.get(reverse("calculate_rf")).status_code, 405)

    def test_invalid_json_is_rejected(self) -> None:
        response = self.client.post(
            reverse("calculate_rf"), data="{not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_lines_is_rejected(self) -> None:
        self.assertEqual(self._post({"spots": [{"x": 1, "y": 2}]}).status_code, 400)

    def test_missing_spots_is_rejected(self) -> None:
        self.assertEqual(self._post({"lines": [{"type": "min", "y": 1}]}).status_code, 400)

    def test_equal_min_max_is_rejected(self) -> None:
        payload = _valid_payload()
        payload["lines"] = [{"type": "min", "y": 100}, {"type": "max", "y": 100}]
        self.assertEqual(self._post(payload).status_code, 400)

    def test_undecodable_local_image_is_rejected(self) -> None:
        payload = _valid_payload()
        payload["imageData"] = "data:image/png;base64,@@notbase64@@"
        self.assertEqual(self._post(payload).status_code, 400)

    def test_missing_eln_image_src_is_rejected(self) -> None:
        payload = _valid_payload()
        payload["isLocalImage"] = False
        payload.pop("imageSrc")
        payload.pop("imageData")
        self.assertEqual(self._post(payload).status_code, 400)

    def test_unknown_eln_image_is_rejected(self) -> None:
        payload = _valid_payload()
        payload["isLocalImage"] = False
        payload["imageSrc"] = "does_not_exist.png"
        payload.pop("imageData")
        self.assertEqual(self._post(payload).status_code, 400)

    def test_unreadable_media_file_is_rejected(self) -> None:
        # A file that exists under MEDIA_ROOT but isn't a decodable image.
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        junk = os.path.join(settings.MEDIA_ROOT, "not_an_image_test.png")
        with open(junk, "wb") as fh:
            fh.write(b"this is not a PNG")
        try:
            payload = _valid_payload()
            payload["isLocalImage"] = False
            payload["imageSrc"] = "not_an_image_test.png"
            payload.pop("imageData")
            self.assertEqual(self._post(payload).status_code, 400)
        finally:
            os.remove(junk)

    def test_oversized_image_is_rejected(self) -> None:
        with mock.patch("tlc.views.MAX_IMAGE_PIXELS", 1000):  # 300x300 exceeds this
            self.assertEqual(self._post(_valid_payload()).status_code, 400)

    def test_rf_outside_zero_to_one_is_skipped(self) -> None:
        payload = _valid_payload()
        payload["lines"] = [{"type": "min", "y": 200}, {"type": "max", "y": 0}]
        payload["spots"] = [{"x": 150, "y": 200}]  # rf == 0 -> filtered out
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rf_values"], [])

    def test_rotation_180_swaps_nothing_270_swaps_dims(self) -> None:
        # 180° keeps WxH; 270° swaps them. Exercises both _rotate_image branches.
        for rotation, expected in ((180, (600, 400)), (270, (400, 600))):
            payload = _valid_payload()
            payload["imageData"] = _png_data_url(width=400, height=600)
            payload["rotation"] = rotation
            response = self._post(payload)
            self.assertEqual(response.status_code, 200)
            name = response.json()["annotated_image_url"].split("/")[-1]
            annotated = cv2.imread(os.path.join(settings.MEDIA_ROOT, name))
            assert annotated is not None
            self.assertEqual(annotated.shape[:2], expected)

    def test_eln_media_image_is_loaded_and_computed(self) -> None:
        # The ELN branch: a real image already under MEDIA_ROOT is read,
        # rotated, and used for the RF calculation (not just e2e-tested).
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        name = "eln_source_test.png"
        path = os.path.join(settings.MEDIA_ROOT, name)
        cv2.imwrite(path, np.full((300, 300, 3), 255, dtype=np.uint8))
        try:
            payload = _valid_payload()
            payload["isLocalImage"] = False
            payload["imageSrc"] = name
            payload.pop("imageData")
            response = self._post(payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["rf_values"][0]["rf"], 0.5)
        finally:
            os.remove(path)


class DetectSpotsErrorTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_get_is_rejected(self) -> None:
        self.assertEqual(self.client.get(reverse("detect_spots")).status_code, 405)

    def test_invalid_json_is_rejected(self) -> None:
        response = self.client.post(
            reverse("detect_spots"), data="nope", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_image_load_error_is_propagated(self) -> None:
        response = self.client.post(
            reverse("detect_spots"),
            data={"isLocalImage": False},  # no imageSrc
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class FetchImageErrorTests(SimpleTestCase):
    """Remote-fetch failure branches (SSRF is covered separately)."""

    def setUp(self) -> None:
        self.client = Client()

    def test_network_error_returns_400(self) -> None:
        with mock.patch("tlc.views.requests.get", side_effect=requests.RequestException("boom")):
            response = self.client.get(reverse("fetch_image"), {"url": "http://93.184.216.34/a"})
        self.assertEqual(response.status_code, 400)

    def test_oversized_remote_image_is_rejected(self) -> None:
        with (
            mock.patch("tlc.views.MAX_IMAGE_BYTES", 4),
            mock.patch("tlc.views.requests.get") as mock_get,
        ):
            mock_get.return_value = _fake_response(200, body=b"0123456789")  # 10 > 4
            response = self.client.get(reverse("fetch_image"), {"url": "http://93.184.216.34/a"})
        self.assertEqual(response.status_code, 400)

    def test_non_image_content_is_rejected(self) -> None:
        with mock.patch("tlc.views.requests.get") as mock_get:
            mock_get.return_value = _fake_response(200, body=b"definitely not an image")
            response = self.client.get(reverse("fetch_image"), {"url": "http://93.184.216.34/a"})
        self.assertEqual(response.status_code, 400)


class ParseRegionTests(SimpleTestCase):
    def test_non_dict_is_none(self) -> None:
        self.assertIsNone(_parse_region(None))
        self.assertIsNone(_parse_region("x"))

    def test_missing_or_bad_keys_is_none(self) -> None:
        self.assertIsNone(_parse_region({"x": 1, "y": 2}))  # no width/height
        self.assertIsNone(_parse_region({"x": "a", "y": 2, "width": 3, "height": 4}))

    def test_non_positive_size_is_none(self) -> None:
        self.assertIsNone(_parse_region({"x": 0, "y": 0, "width": 0, "height": 5}))

    def test_valid_region_is_parsed(self) -> None:
        self.assertEqual(_parse_region({"x": 1, "y": 2, "width": 3, "height": 4}), (1, 2, 3, 4))


class DecodeDataUrlBranchTests(SimpleTestCase):
    def test_valid_base64_that_is_not_an_image_returns_none(self) -> None:
        payload = base64.b64encode(b"hello, not an image").decode()
        self.assertIsNone(_decode_data_url("data:image/png;base64," + payload))

    def test_payload_over_size_cap_returns_none(self) -> None:
        payload = base64.b64encode(b"0123456789").decode()  # 10 raw bytes
        with mock.patch("tlc.views.MAX_IMAGE_BYTES", 4):
            self.assertIsNone(_decode_data_url("data:image/png;base64," + payload))


class ResolveWithinMediaTests(SimpleTestCase):
    """Path confinement helper for serve-image."""

    def test_empty_or_dot_names_are_rejected(self) -> None:
        self.assertIsNone(_resolve_within_media(""))
        self.assertIsNone(_resolve_within_media("."))
        self.assertIsNone(_resolve_within_media(".."))

    def test_traversal_is_stripped_to_basename(self) -> None:
        # basename() drops the traversal; a bare name resolves inside MEDIA_ROOT.
        resolved = _resolve_within_media("../../etc/passwd")
        assert resolved is not None
        self.assertTrue(resolved.startswith(os.path.realpath(settings.MEDIA_ROOT)))
        self.assertTrue(resolved.endswith("passwd"))


class SafeRemoteUrlTests(SimpleTestCase):
    """SSRF URL validation branches beyond the endpoint-level tests."""

    @override_settings(ELN_ALLOWED_HOSTS=["eln.example.org"])
    def test_allowlist_rejects_unlisted_host(self) -> None:
        self.assertFalse(_is_safe_remote_url("http://93.184.216.34/x.png"))

    @override_settings(ELN_ALLOWED_HOSTS=["93.184.216.34"])
    def test_allowlist_accepts_listed_host_shape(self) -> None:
        # A listed public IP passes the allowlist and public-address checks.
        self.assertTrue(_is_safe_remote_url("http://93.184.216.34/x.png"))

    def test_unresolvable_host_is_rejected(self) -> None:
        with mock.patch("tlc.views.socket.getaddrinfo", side_effect=socket.gaierror):
            self.assertFalse(_is_safe_remote_url("http://host.example/x.png"))


class FetchRedirectTests(SimpleTestCase):
    """Redirect-following helper edge branches."""

    def test_redirect_without_location_returns_none(self) -> None:
        with mock.patch("tlc.views.requests.get") as mock_get:
            mock_get.return_value = _fake_response(302, {})  # 302 but no Location
            self.assertIsNone(_fetch_following_safe_redirects("http://93.184.216.34/a"))

    def test_too_many_redirects_returns_none(self) -> None:
        # Always redirect to a valid public target -> exceeds MAX_REDIRECTS.
        with mock.patch("tlc.views.requests.get") as mock_get:
            mock_get.return_value = _fake_response(302, {"Location": "http://93.184.216.34/next"})
            self.assertIsNone(_fetch_following_safe_redirects("http://93.184.216.34/a"))

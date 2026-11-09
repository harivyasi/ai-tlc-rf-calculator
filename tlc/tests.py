"""Tests for the RF calculation and the security-sensitive image endpoints."""

from __future__ import annotations

import base64
import csv
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
from tlc import results
from tlc.detection import assign_lines
from tlc.views import (
    _decode_data_url,
    _fetch_following_safe_redirects,
    _is_safe_remote_url,
    _parse_region,
    _parse_text_scale,
    _resolve_within_media,
    rf_from_positions,
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

    def test_saved_image_shows_lines_markers_and_spots(self) -> None:
        # SI 8.2: the saved image records the baseline, solvent front, markers
        # and retained spots the result was calculated from, at full resolution.
        payload = {
            "isLocalImage": True,
            "imageData": _png_data_url(width=400, height=600),
            "imageSrc": "sample.png",
            "lines": [
                {"type": "min", "y": 500, "text": "min"},
                {"type": "max", "y": 100, "text": "max"},
                {"type": "horizontal", "y": 300, "text": "SM"},
                {"type": "vertical", "x": 50, "text": "A"},
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

        def row_is(y: int, bgr: tuple[int, int, int]) -> bool:
            return bool(np.all(annotated[y, 300:390] == bgr, axis=1).all())

        self.assertTrue(row_is(500, results._GREEN), "baseline (Min) not drawn")
        self.assertTrue(row_is(100, results._RED), "solvent front (Max) not drawn")
        self.assertTrue(row_is(300, results._BLACK), "H-line not drawn in black")
        self.assertTrue(np.all(annotated[50:450, 50] == results._BLUE, axis=1).any(), "V-line")
        self.assertTrue(np.all(annotated == results._ORANGE, axis=2).any(), "spot ring")

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


_BLOBS = [(120, 120), (360, 130), (240, 360)]


def _post_json(client: Client, name: str, payload: dict[str, Any]) -> Any:
    return client.post(reverse(name), data=payload, content_type="application/json")


class DetectSpotsTests(SimpleTestCase):
    """TLC-SpotDet through /detect-spots/ (loads the real model).

    The synthetic circles are not TLC spots, but the model reliably finds them
    (confidence ~0.95), which is enough to pin the band and region logic.
    """

    def setUp(self) -> None:
        self.client = Client()

    def _detect(self, centers: list[tuple[int, int]], **extra: Any) -> Any:
        payload = {"isLocalImage": True, "imageData": _blobs_data_url(centers), **extra}
        return _post_json(self.client, "detect_spots", payload)

    def test_finds_spots_between_min_and_max(self) -> None:
        response = self._detect(_BLOBS, minY=480, maxY=20)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], len(_BLOBS))
        for spot in body["spots"]:
            self.assertTrue(
                any(abs(spot["x"] - cx) < 6 and abs(spot["y"] - cy) < 6 for cx, cy in _BLOBS),
                f"unexpected spot {spot}",
            )
            self.assertGreaterEqual(spot["conf"], 0.25)

    def test_only_the_band_between_the_lines_is_searched(self) -> None:
        # The blob at y=360 lies below the baseline at y=300.
        body = self._detect(_BLOBS, minY=300, maxY=50).json()
        self.assertEqual(sorted(round(s["y"], -1) for s in body["spots"]), [120, 130])
        self.assertEqual(body["roi"], [0, 50 - 12, 500, 300 + 12])  # TLC_BAND_MARGIN

    def test_line_order_does_not_matter_for_the_band(self) -> None:
        a = self._detect(_BLOBS, minY=300, maxY=50).json()["spots"]
        b = self._detect(_BLOBS, minY=50, maxY=300).json()["spots"]
        self.assertEqual(a, b)

    def test_plate_area_limits_the_x_range(self) -> None:
        region = {"x": 0, "y": 0, "width": 300, "height": 500}  # excludes x=360
        body = self._detect(_BLOBS, minY=480, maxY=20, region=region).json()
        self.assertEqual(sorted(round(s["x"], -1) for s in body["spots"]), [120, 240])
        self.assertTrue(all(s["x"] < 300 for s in body["spots"]))

    def test_blank_image_yields_no_spots(self) -> None:
        response = self._detect([], minY=480, maxY=20)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)

    def test_min_and_max_are_required(self) -> None:
        for extra in ({}, {"minY": 400}, {"minY": "a", "maxY": 20}):
            response = self._detect(_BLOBS, **extra)
            self.assertEqual(response.status_code, 400, extra)
            self.assertIn("Min and Max", response.json()["error"])

    def test_a_band_outside_the_image_is_rejected(self) -> None:
        # Shilpa's rule: the strip (band +/- margin, clipped to the image) must
        # be more than 2 px tall. Here it lies wholly above the image.
        response = self._detect(_BLOBS, minY=-50, maxY=-100)
        self.assertEqual(response.status_code, 400)

    def test_a_degenerate_band_still_searches_the_margin(self) -> None:
        # Min == Max still leaves a 24 px strip, as in Shilpa's tool; calculate_rf
        # is what refuses coincident lines.
        body = self._detect(_BLOBS, minY=120, maxY=120).json()
        self.assertEqual(body["roi"], [0, 108, 500, 132])

    def test_response_identifies_the_model(self) -> None:
        model = self._detect([], minY=480, maxY=20).json()["model"]
        self.assertEqual(model["name"], "TLC-SpotDet")
        self.assertEqual(model["file"], "spot_best.onnx")
        self.assertEqual(model["sha256"], SPOT_SHA256)
        self.assertEqual(model["imgsz"], 1280)


SPOT_SHA256 = "cc0d979487ef228d5cb106c7e75d80787d94b41d67653b08ee70d60e028019c5"
LINE_SHA256 = "895bd9e1971ed74c58bbb03fe78a6ba84b6f46747775e88c20d8239dd38b3869"


def _det(cls: int, y: float, conf: float) -> dict[str, Any]:
    return {"x1": 0.0, "y1": y - 60, "x2": 100.0, "y2": y + 60, "conf": conf, "cls": cls}


class LineAssignmentTests(SimpleTestCase):
    """Shilpa's baseline/front rules, without the model (tlc.detection.assign_lines)."""

    MIN, MAX = 1, 0  # the deployed model's ids (SI Table S2)

    def _assign(self, dets: list[dict[str, Any]], height: int = 1000) -> Any:
        return assign_lines(dets, height, min_cls=self.MIN, max_cls=self.MAX)

    def test_lower_line_is_the_baseline_whatever_its_class(self) -> None:
        # Classes swapped relative to position: position wins.
        result = self._assign([_det(self.MIN, 100, 0.9), _det(self.MAX, 800, 0.8)])
        self.assertEqual(result, {"minY": 800, "maxY": 100, "estimated": None})

    def test_most_confident_box_per_class_is_used(self) -> None:
        dets = [
            _det(self.MIN, 850, 0.3),
            _det(self.MIN, 820, 0.7),
            _det(self.MAX, 150, 0.6),
            _det(self.MAX, 90, 0.2),
        ]
        self.assertEqual(self._assign(dets), {"minY": 820, "maxY": 150, "estimated": None})

    def test_single_upper_line_is_the_front_and_baseline_is_estimated(self) -> None:
        result = self._assign([_det(self.MAX, 120, 0.9)])
        self.assertEqual(result, {"minY": 900.0, "maxY": 120, "estimated": "min"})

    def test_single_lower_line_is_the_baseline_and_front_is_estimated(self) -> None:
        result = self._assign([_det(self.MAX, 700, 0.9)])
        self.assertEqual(result, {"minY": 700, "maxY": 100.0, "estimated": "max"})

    def test_unknown_classes_and_no_detections_give_none(self) -> None:
        self.assertIsNone(self._assign([]))
        self.assertIsNone(self._assign([_det(7, 500, 0.9)]))


class DetectLinesTests(SimpleTestCase):
    """/detect-lines/ contract. The model needs a real plate to find lines, so
    the success path stubs inference; the class mapping is checked on the real
    session."""

    def setUp(self) -> None:
        self.client = Client()

    def test_real_model_declares_max_then_min(self) -> None:
        from tlc.inference import line_model

        model, info = line_model()
        self.assertEqual(model.names, {0: "max_line", 1: "min_line"})
        self.assertEqual((model.class_id("min_line"), model.class_id("max_line")), (1, 0))
        self.assertEqual(info.sha256, LINE_SHA256)
        self.assertEqual(info.imgsz, 1024)

    def test_blank_image_reports_no_lines(self) -> None:
        payload = {"isLocalImage": True, "imageData": _blobs_data_url([])}
        response = _post_json(self.client, "detect_lines", payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("No reference lines", response.json()["error"])

    def test_detected_lines_are_returned_in_image_coordinates(self) -> None:
        from tlc.inference import line_model

        model, _ = line_model()
        fake = [_det(1, 430, 0.8), _det(0, 60, 0.7)]
        with mock.patch.object(model, "predict", return_value=fake):
            payload = {"isLocalImage": True, "imageData": _blobs_data_url([])}
            body = _post_json(self.client, "detect_lines", payload).json()
        self.assertEqual((body["minY"], body["maxY"], body["estimated"]), (430, 60, None))
        self.assertEqual(body["model"]["name"], "TLC-LineDet")

    def test_plate_area_crops_the_input_and_offsets_the_result(self) -> None:
        from tlc.inference import line_model

        model, _ = line_model()
        fake = [_det(1, 150, 0.8), _det(0, 20, 0.7)]  # in plate-box coordinates
        payload = {
            "isLocalImage": True,
            "imageData": _blobs_data_url([]),  # 500 x 500
            "region": {"x": 100, "y": 200, "width": 300, "height": 250},
        }
        with mock.patch.object(model, "predict", return_value=fake) as predict:
            body = _post_json(self.client, "detect_lines", payload).json()
        self.assertEqual(predict.call_args.args[0].shape[:2], (250, 300))
        self.assertEqual((body["minY"], body["maxY"]), (350, 220))

    def test_plate_area_estimate_refers_to_the_box(self) -> None:
        from tlc.inference import line_model

        model, _ = line_model()
        payload = {
            "isLocalImage": True,
            "imageData": _blobs_data_url([]),
            "region": {"x": 0, "y": 100, "width": 500, "height": 200},
        }
        with mock.patch.object(model, "predict", return_value=[_det(0, 30, 0.9)]):
            body = _post_json(self.client, "detect_lines", payload).json()
        # Upper half of the box -> the front; baseline estimated at 90 % of it.
        self.assertEqual((body["maxY"], body["minY"], body["estimated"]), (130, 280.0, "min"))

    def test_inference_failure_is_a_500_without_details(self) -> None:
        from tlc.inference import line_model

        model, _ = line_model()
        with mock.patch.object(model, "predict", side_effect=RuntimeError("secret detail")):
            payload = {"isLocalImage": True, "imageData": _blobs_data_url([])}
            with self.assertLogs("tlc.views", level="ERROR"):
                response = _post_json(self.client, "detect_lines", payload)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.json()["error"])


class ModelLoadingTests(SimpleTestCase):
    def test_concurrent_first_use_builds_one_session(self) -> None:
        import threading

        from tlc import inference

        built: list[str] = []

        class Fake:
            imgsz = 1

            def __init__(self, path: str, sess_options: object = None) -> None:
                built.append(path)
                time.sleep(0.05)  # widen the race window
                self.sess = mock.Mock()
                self.sess.get_modelmeta.return_value.custom_metadata_map = {}

        with mock.patch.dict(inference._sessions, clear=True):
            threads = [
                threading.Thread(
                    target=inference._load,
                    args=("k", "Fake", settings.SPOT_MODEL_PATH, Fake),
                )
                for _ in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(built), 1)


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

    def test_both_image_sources_are_analysed_upright(self) -> None:
        """A sideways-stored phone photo (EXIF 6 and 8, JPEG and WebP) is
        analysed as the browser shows it, whether uploaded or from the ELN.
        Checked through calculate_rf: the saved annotated image is upright."""
        client = Client()
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        for fmt, orientation in (("JPEG", 6), ("JPEG", 8), ("WEBP", 6)):
            stored = Image.new("RGB", (300, 200), (230, 230, 230))  # landscape
            exif = stored.getexif()
            exif[274] = orientation
            buf = io.BytesIO()
            stored.save(buf, format=fmt, exif=exif)
            raw = buf.getvalue()
            name = f"exif_test_{orientation}.{fmt.lower()}"
            with open(os.path.join(settings.MEDIA_ROOT, name), "wb") as fh:
                fh.write(raw)
            mime = f"image/{fmt.lower()}"
            sources = {
                "upload": {
                    "isLocalImage": True,
                    "imageSrc": name,
                    "imageData": f"data:{mime};base64," + base64.b64encode(raw).decode(),
                },
                "eln": {"isLocalImage": False, "imageSrc": name},
            }
            try:
                for source, image in sources.items():
                    payload = image | {
                        "lines": [{"type": "min", "y": 250}, {"type": "max", "y": 50}],
                        "spots": [{"x": 100, "y": 150}],
                    }
                    response = client.post(
                        reverse("calculate_rf"), data=payload, content_type="application/json"
                    )
                    self.assertEqual(response.status_code, 200, (fmt, orientation, source))
                    out = response.json()["annotated_image_url"].split("/")[-1]
                    saved = cv2.imread(os.path.join(settings.MEDIA_ROOT, out))
                    assert saved is not None
                    os.remove(os.path.join(settings.MEDIA_ROOT, out))
                    os.remove(glob.glob(os.path.join(settings.MEDIA_ROOT, "rf_values_*.csv"))[0])
                    self.assertEqual(saved.shape[:2], (300, 200), (fmt, orientation, source))
            finally:
                os.remove(os.path.join(settings.MEDIA_ROOT, name))

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


class SpotThresholdTests(SimpleTestCase):
    def test_setting_states_the_cutoff_built_into_the_model(self) -> None:
        """spot_best.onnx returns no scores below 0.25, so a lower setting
        would claim a sensitivity it does not have."""
        self.assertEqual(settings.SPOT_CONF_DEFAULT, 0.25)


class TextScaleTests(SimpleTestCase):
    """Annotation text-size multiplier: parsing/clamping and its effect."""

    def test_parse_text_scale_clamps_and_defaults(self) -> None:
        self.assertEqual(_parse_text_scale(None), 1.0)  # missing -> 100%
        self.assertEqual(_parse_text_scale("not a number"), 1.0)  # malformed -> 100%
        self.assertEqual(_parse_text_scale(2.5), 2.5)  # in range, passes through
        self.assertEqual(_parse_text_scale(100), 5.0)  # above cap -> clamped to 500%
        self.assertEqual(_parse_text_scale(-3), 0.1)  # below floor -> clamped to 10%

    def test_larger_text_scale_draws_more_label_pixels(self) -> None:
        row = {"n": 1, "x": 200, "y": 200, "rf": 0.5, "compound": "Aspirin", "solvent": "EtOAc"}

        def ink(scale: float) -> int:
            img = np.full((400, 400, 3), 128, dtype=np.uint8)
            results.draw_annotations(
                img, [row], min_y=390, max_y=10, markers=[], text_scale=scale, full_labels=True
            )
            return int(np.any(img != 128, axis=2).sum())

        self.assertGreater(ink(3.0), ink(1.0))


def _marker(kind_: str, pos: float, text: str = "", kind: str = "") -> dict[str, Any]:
    axis = "y" if kind_ == "horizontal" else "x"
    return {"type": kind_, axis: pos, "text": text, "kind": kind}


class ResultRowsTests(SimpleTestCase):
    """Ordering, numbering and labelling of result rows (tlc.results)."""

    def test_rows_are_sorted_by_lane_label_naturally_then_rf(self) -> None:
        lanes = [
            _marker("vertical", 100, "Lane 10"),
            _marker("vertical", 200, "Lane 2"),
            _marker("vertical", 300, "lane 1"),
        ]
        spots: list[tuple[float, float, float]] = [
            (100, 0, 0.3),
            (200, 0, 0.7),
            (200, 0, 0.2),
            (300, 0, 0.5),
            (999, 0, 0.9),
        ]
        rows = results.build_rows(spots, lanes[:2] + lanes[2:])
        self.assertEqual(
            [(r["n"], r["solvent"], r["rf"]) for r in rows],
            [
                (1, "lane 1", 0.5),
                (2, "lane 1", 0.9),  # x=999 is nearest to lane 1 at x=300
                (3, "Lane 2", 0.2),
                (4, "Lane 2", 0.7),
                (5, "Lane 10", 0.3),
            ],
        )

    def test_unlabelled_lanes_sort_last(self) -> None:
        rows = results.build_rows(
            [(10, 0, 0.1), (500, 0, 0.2)],
            [_marker("vertical", 10, ""), _marker("vertical", 500, "B")],
        )
        self.assertEqual([r["solvent"] for r in rows], ["B", ""])

    def test_types_and_unknown_compound(self) -> None:
        markers = [
            _marker("vertical", 0, "R1", "product 1"),
            _marker("horizontal", 100, "", "expected product"),
            _marker("horizontal", 500, "", ""),
        ]
        rows = results.build_rows([(0, 110, 0.4), (0, 490, 0.1)], markers)
        typed, bare = sorted(rows, key=lambda r: -r["rf"])
        self.assertEqual((typed["solvent"], typed["solvent_type"]), ("R1", "product 1"))
        # A marker with only a type names the compound by its type column.
        self.assertEqual((typed["compound"], typed["compound_type"]), ("", "expected product"))
        # A marker with neither text nor type is as good as none.
        self.assertEqual((bare["compound"], bare["compound_type"]), ("Unknown", ""))

    def test_no_markers(self) -> None:
        (row,) = results.build_rows([(5, 5, 0.5)], [])
        self.assertEqual((row["n"], row["solvent"], row["compound"]), (1, "", "Unknown"))

    def test_labels_are_cleaned(self) -> None:
        self.assertEqual(results.clean_label("  a \n b  "), "a b")
        self.assertEqual(results.clean_label(42), "")
        self.assertEqual(len(results.clean_label("x" * 500)), results.MAX_LABEL_CHARS)


class ResultCsvTests(SimpleTestCase):
    def _rows(self) -> list[dict[str, Any]]:
        return results.build_rows(
            [(0, 0, 0.5), (300, 0, 0.25)],
            [
                _marker("vertical", 0, "A, crude", "product 1"),
                _marker("vertical", 300, '=HYPERLINK("x")', ""),
                _marker("horizontal", 0, "SM", "starting material"),
            ],
        )

    def test_layout_matches_the_table_and_keeps_the_chmo_and_details_lines(self) -> None:
        text = results.csv_text(self._rows())
        lines = text.split("\n")
        self.assertEqual(lines[0], "Measurement type;thin layer chromatography (TLC);CHMO:0001007")
        self.assertEqual(lines[1], "")
        self.assertEqual(
            lines[2],
            "spot,mixture_label (X axis),mixture_type,substance_label (Y axis),"
            "substance_type,RF value",
        )
        self.assertIn("Details:", lines)
        self.assertIn("TLC (solvents); Rf (UV-Vis in nm, staining) = ", text)

    def test_cells_are_quoted_and_formula_safe(self) -> None:
        text = results.csv_text(self._rows())
        table = text.split("\n\n")[1]
        rows = list(csv.reader(io.StringIO(table)))
        header, *body = rows
        self.assertEqual(len(header), 6)
        self.assertTrue(all(len(row) == 6 for row in body), body)
        by_lane = {row[1]: row for row in body}
        self.assertEqual(
            by_lane["A, crude"], ["2", "A, crude", "product 1", "SM", "starting material", "0.5"]
        )
        self.assertIn("'=HYPERLINK", next(k for k in by_lane if "HYPERLINK" in k))

    def test_served_csv_matches_the_response(self) -> None:
        payload = _valid_payload()
        response = Client().post(
            reverse("calculate_rf"), data=payload, content_type="application/json"
        )
        body = response.json()
        name = body["csv_url"].split("/")[-1]
        with open(os.path.join(settings.MEDIA_ROOT, name), encoding="utf-8") as fh:
            served = fh.read()
        os.remove(os.path.join(settings.MEDIA_ROOT, name))
        os.remove(os.path.join(settings.MEDIA_ROOT, body["annotated_image_url"].split("/")[-1]))
        self.assertEqual(served, results.csv_text(body["rf_values"]))
        self.assertEqual(body["rf_values"][0]["n"], 1)


class ResultImageLabelTests(SimpleTestCase):
    def test_full_labels_add_text_beyond_the_number(self) -> None:
        row = {"n": 7, "x": 100, "y": 200, "rf": 0.5, "compound": "SM", "solvent": "A"}

        def ink(full: bool) -> int:
            img = np.full((400, 400, 3), 128, dtype=np.uint8)
            results.draw_annotations(img, [row], min_y=390, max_y=10, markers=[], full_labels=full)
            return int(np.any(img != 128, axis=2).sum())

        self.assertGreater(ink(True), ink(False))

    def test_text_has_a_white_halo(self) -> None:
        # On a green plate black text alone is hard to read. OpenCV 5 ignores
        # putText thickness, which once hid the halo entirely.
        img = np.full((60, 120, 3), (0, 200, 0), dtype=np.uint8)
        results._text(img, "10", (10, 45), 1.2, 3)
        self.assertGreater(int(np.all(img == 255, axis=2).sum()), 100, "no halo")
        self.assertGreater(int(np.all(img < 30, axis=2).sum()), 100, "no text")

    def test_text_at_the_image_edge_is_clipped_not_an_error(self) -> None:
        img = np.full((40, 40, 3), 128, dtype=np.uint8)
        results._text(img, "Lane 12 (starting material)", (30, 38), 1.0, 2)
        results._text(img, "x", (-500, -500), 1.0, 2)  # wholly outside

    def test_close_lane_captions_are_staggered(self) -> None:
        img = np.full((300, 300, 3), 128, dtype=np.uint8)
        markers = [
            {"type": "vertical", "x": 100, "text": "Lane one", "kind": ""},
            {"type": "vertical", "x": 110, "text": "Lane two", "kind": ""},
        ]
        results.draw_annotations(img, [], min_y=250, max_y=20, markers=markers)
        # Two caption rows means ink in both bottom bands right of the lines.
        bottom, second = img[275:295, 115:200], img[253:273, 115:200]
        self.assertTrue(np.any(bottom != 128) and np.any(second != 128))

    def test_marker_caption(self) -> None:
        self.assertEqual(results.marker_caption({"text": "A", "kind": "reagent"}), "A (reagent)")
        self.assertEqual(results.marker_caption({"text": "", "kind": "reagent"}), "reagent")
        self.assertEqual(results.marker_caption({"text": "A", "kind": ""}), "A")


class RfEquationTests(SimpleTestCase):
    """SI section 7.3: Eq. 8 and the retention rule of Eq. 9."""

    def rf(self, spot: float, baseline: float = 900, front: float = 100) -> float | None:
        return rf_from_positions(y_baseline=baseline, y_front=front, y_spot=spot)

    def test_equation_8(self) -> None:
        # d_spot / d_front = (900 - 500) / (900 - 100)
        self.assertEqual(self.rf(500), 0.5)
        self.assertEqual(self.rf(700), 0.25)
        self.assertEqual(self.rf(260), 0.8)

    def test_retention_is_inclusive_and_tested_before_rounding(self) -> None:
        self.assertEqual(self.rf(900), 0.0)
        self.assertEqual(self.rf(100), 1.0)
        self.assertEqual(self.rf(103), 1.0)  # Rf 0.99625: kept, reported as 1.0
        self.assertIsNone(self.rf(900.5))  # just below the baseline
        self.assertIsNone(self.rf(99.5))  # just above the front

    def test_matches_shilpas_tool_inside_the_open_interval(self) -> None:
        # Her rule (round, then keep 0 < Rf < 1) agrees everywhere it kept a spot.
        for spot in range(105, 896, 7):
            shilpa = round((900 - spot) / (900 - 100), 2)
            if 0.0 < shilpa < 1.0:
                self.assertEqual(self.rf(spot), shilpa, spot)


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

    def test_spots_outside_the_interval_are_skipped_but_its_ends_kept(self) -> None:
        # SI Eq. 9: 0 <= Rf <= 1 is kept. On the baseline is Rf 0 (the compound
        # did not move); on the front is Rf 1. Below/above them is excluded.
        payload = _valid_payload()
        payload["lines"] = [{"type": "min", "y": 200}, {"type": "max", "y": 0}]
        payload["spots"] = [
            {"x": 10, "y": 200},  # on the baseline -> 0.0
            {"x": 20, "y": 0},  # on the front -> 1.0
            {"x": 30, "y": 201},  # below the baseline -> excluded
            {"x": 40, "y": -1},  # above the front -> excluded
        ]
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([v["rf"] for v in response.json()["rf_values"]], [0.0, 1.0])

    def test_min_above_max_is_rejected(self) -> None:
        # SI Eq. 4: the baseline must be below the solvent front.
        payload = _valid_payload()
        payload["lines"] = [{"type": "min", "y": 0}, {"type": "max", "y": 200}]
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("baseline below", response.json()["error"])

    def test_non_numeric_positions_are_rejected(self) -> None:
        for lines, spots in (
            ([{"type": "min", "y": "200"}, {"type": "max", "y": 0}], [{"x": 1, "y": 100}]),
            ([{"type": "min", "y": 200}, {"type": "max", "y": 0}], [{"x": 1}]),
            ([{"type": "min", "y": 200}, {"type": "max", "y": 0}], [{"x": True, "y": 100}]),
        ):
            payload = _valid_payload()
            payload["lines"], payload["spots"] = lines, spots
            self.assertEqual(self._post(payload).status_code, 400, (lines, spots))

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
        self.assertEqual(self.client.get(reverse("detect_lines")).status_code, 405)

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


class ImageLimitTests(SimpleTestCase):
    """Real plate photos reach 13 MB / 50 MP; the limits must admit them, and
    the browser must enforce the same file cap as the server."""

    def test_defaults_admit_a_50_megapixel_13_megabyte_photo(self) -> None:
        from tlc import views

        self.assertGreaterEqual(views.MAX_IMAGE_PIXELS, 6120 * 8160)
        self.assertGreaterEqual(views.MAX_IMAGE_BYTES, 13 * 1024 * 1024)

    def test_request_body_cap_fits_a_base64_encoded_maximum_image(self) -> None:
        encoded = -(-settings.MAX_IMAGE_MB * 1024 * 1024 // 3) * 4
        self.assertGreater(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, encoded)

    def test_page_passes_the_file_cap_to_the_browser(self) -> None:
        html = Client().get(reverse("upload_image")).content.decode()
        self.assertIn(f'data-max-image-mb="{settings.MAX_IMAGE_MB}"', html)

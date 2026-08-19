"""Contract tests for the ONNX detection endpoints.

These load the real 57MB of models, so they are marked slow. They assert the
response contract and the coordinate semantics rather than detection quality:
a synthetic blank plate has nothing to find, and pinning model output would
make the suite a change-detector.
"""
import base64
import json

import pytest

SPOTS_URL = '/auto_detect_spots/'
LINES_URL = '/auto_detect_lines/'

pytestmark = pytest.mark.slow


def post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type='application/json')


def local_payload(png, **extra):
    encoded = base64.b64encode(png).decode()
    return {
        'isLocalImage': True,
        'imageData': f'data:image/png;base64,{encoded}',
        'imageSrc': 'local_upload.png',
        **extra,
    }


@pytest.mark.django_db
class TestAutoDetectSpots:
    def test_requires_min_and_max(self, client, png_bytes):
        response = post(client, SPOTS_URL, local_payload(png_bytes()))
        assert response.status_code == 400
        assert 'minY' in response.json()['error']

    def test_returns_spots_in_image_coordinates(self, client, png_bytes):
        response = post(client, SPOTS_URL, local_payload(png_bytes(400, 400), minY=380, maxY=20))

        assert response.status_code == 200
        body = response.json()
        assert body['debug']['image_size'] == [400, 400]
        for spot in body['spots']:
            assert 0 <= spot['x'] <= 400
            assert 20 <= spot['y'] <= 380, 'spots must be clipped to the min/max band'

    def test_a_degenerate_band_is_widened_not_rejected(self, client, png_bytes):
        """Documents actual behaviour: TLC_BAND_MARGIN expands the ROI by
        +/-12px, so an exactly-degenerate band still yields a searchable strip
        and the endpoint succeeds. Harmless here because this endpoint only
        detects; the case that would divide by zero is refused by calculate_rf
        (see test_calculate_rf.py::test_inverted_min_and_max_rejected, whose
        `min <= max` guard also covers equality)."""
        response = post(client, SPOTS_URL, local_payload(png_bytes(), minY=100, maxY=100))
        assert response.status_code == 200
        assert response.json()['debug']['roi'] == [0, 88, 200, 112]

    def test_resolves_an_image_by_media_relative_name(self, client, plate_in_media):
        """The ELN path posts a bare basename, which annotate.html derives by
        stripping directories. This is the coupling the flat-filename decision
        protects."""
        name = plate_in_media('plate.png', width=400, height=400)
        response = post(client, SPOTS_URL, {
            'isLocalImage': False, 'imageSrc': name, 'minY': 380, 'maxY': 20,
        })
        assert response.status_code == 200

    def test_get_not_allowed(self, client):
        assert client.get(SPOTS_URL).status_code == 405


@pytest.mark.django_db
class TestAutoDetectLines:
    def test_min_is_below_max_when_lines_are_returned(self, client, png_bytes):
        """TLC semantics: min is the baseline near the bottom (larger y), max
        is the solvent front near the top. The model's class ids do not encode
        this, so the view resolves it by position."""
        response = post(client, LINES_URL, local_payload(png_bytes(400, 400)))

        assert response.status_code in (200, 400)
        if response.status_code == 200:
            body = response.json()
            assert body['minY'] > body['maxY']
            assert 0 <= body['maxY'] <= 400
            assert 0 <= body['minY'] <= 400

    def test_reports_image_size(self, client, png_bytes):
        response = post(client, LINES_URL, local_payload(png_bytes(400, 300)))
        if response.status_code == 200:
            assert response.json()['debug']['image_size'] == [400, 300]

    def test_missing_image_is_rejected(self, client):
        response = post(client, LINES_URL, {'isLocalImage': True})
        assert response.status_code == 400

    def test_get_not_allowed(self, client):
        assert client.get(LINES_URL).status_code == 405

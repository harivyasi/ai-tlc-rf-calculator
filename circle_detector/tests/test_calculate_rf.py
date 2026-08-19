"""Characterisation tests for the RF calculation.

These lock in behaviour that must NOT change while the security fixes land.
They were written against the pre-hardening code and pass there; the golden
value below was captured from a running container before any changes.
"""
import base64
import csv
import json

import pytest

URL = '/calculate_rf/'


def post(client, payload):
    return client.post(URL, data=json.dumps(payload), content_type='application/json')


@pytest.mark.django_db
def test_golden_rf_value(client, plate_in_media, rf_payload):
    """The primary regression anchor: this exact value came off a running
    container before the refactor."""
    plate_in_media()
    response = post(client, rf_payload())

    assert response.status_code == 200
    body = response.json()
    assert [entry['rf'] for entry in body['rf_values']] == [0.5]


@pytest.mark.django_db
def test_rf_is_measured_from_baseline_upwards(client, plate_in_media, rf_payload):
    """A spot nearer the baseline has a lower RF than one nearer the front.
    Guards the min/max semantics, which an earlier version of this code had
    inverted."""
    plate_in_media()
    response = post(client, rf_payload(spots=[
        {'x': 50, 'y': 160},   # just above the baseline -> low RF
        {'x': 100, 'y': 100},  # midway -> 0.5
        {'x': 150, 'y': 40},   # near the front -> high RF
    ]))

    values = [entry['rf'] for entry in response.json()['rf_values']]
    assert values == sorted(values, reverse=False) or values == sorted(values)
    assert values == [0.12, 0.5, 0.88]


@pytest.mark.django_db
def test_rf_rounded_to_two_decimals(client, plate_in_media, rf_payload):
    plate_in_media()
    response = post(client, rf_payload(spots=[{'x': 10, 'y': 123}]))

    rf = response.json()['rf_values'][0]['rf']
    assert rf == round(rf, 2)


@pytest.mark.django_db
def test_values_outside_zero_to_one_are_skipped(client, plate_in_media, rf_payload):
    """Spots below the baseline or above the front are dropped, not clamped."""
    plate_in_media()
    response = post(client, rf_payload(spots=[
        {'x': 10, 'y': 190},  # below baseline -> negative
        {'x': 20, 'y': 100},  # valid
        {'x': 30, 'y': 10},   # above the front -> > 1
    ]))

    body = response.json()
    assert [entry['rf'] for entry in body['rf_values']] == [0.5]
    assert body['debug_info']['spots_processed'] == 3
    assert body['debug_info']['valid_rf_values'] == 1


@pytest.mark.django_db
def test_labels_come_from_nearest_reference_lines(client, plate_in_media, rf_payload):
    """The vertical line nearest in x supplies the solvent, the horizontal
    line nearest in y supplies the compound."""
    plate_in_media()
    response = post(client, rf_payload(lines=[
        {'type': 'min', 'y': 180},
        {'type': 'max', 'y': 20},
        {'type': 'vertical', 'x': 20, 'text': 'Solvent A'},
        {'type': 'vertical', 'x': 180, 'text': 'Solvent B'},
        {'type': 'horizontal', 'y': 30, 'text': 'Compound Top'},
        {'type': 'horizontal', 'y': 170, 'text': 'Compound Bottom'},
    ], spots=[{'x': 175, 'y': 105}]))

    entry = response.json()['rf_values'][0]
    assert entry['solvent'] == 'Solvent B'
    assert entry['compound'] == 'Compound Bottom'


@pytest.mark.django_db
def test_unlabelled_spots_report_unknown(client, plate_in_media, rf_payload):
    plate_in_media()
    entry = post(client, rf_payload()).json()['rf_values'][0]
    assert entry['solvent'] == 'Unknown'
    assert entry['compound'] == 'Unknown'


@pytest.mark.django_db
def test_inverted_min_and_max_rejected(client, plate_in_media, rf_payload):
    """min must be the bottom baseline. Swapping them is a user error, not a
    silently negative RF."""
    plate_in_media()
    response = post(client, rf_payload(
        lines=[{'type': 'min', 'y': 20}, {'type': 'max', 'y': 180}],
    ))
    assert response.status_code == 400
    assert 'Min' in response.json()['error']


@pytest.mark.django_db
def test_equal_min_and_max_rejected(client, plate_in_media, rf_payload):
    """Coincident lines mean zero solvent travel. The guard is `min <= max`,
    so this is refused before the division that would raise."""
    plate_in_media()
    response = post(client, rf_payload(
        lines=[{'type': 'min', 'y': 100}, {'type': 'max', 'y': 100}],
    ))
    assert response.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize('overrides,missing', [
    ({'lines': []}, 'lines'),
    ({'spots': []}, 'spots'),
    ({'lines': [{'type': 'min', 'y': 180}]}, 'max'),
    ({'lines': [{'type': 'max', 'y': 20}]}, 'min'),
])
def test_missing_inputs_rejected(client, plate_in_media, rf_payload, overrides, missing):
    plate_in_media()
    response = post(client, rf_payload(**overrides))
    assert response.status_code == 400


@pytest.mark.django_db
def test_malformed_json_rejected(client):
    response = client.post(URL, data='not json', content_type='application/json')
    assert response.status_code == 400


@pytest.mark.django_db
def test_get_not_allowed(client):
    assert client.get(URL).status_code == 405


@pytest.mark.django_db
def test_coordinates_scale_from_canvas_to_image(client, plate_in_media, rf_payload):
    """The browser sends canvas coordinates; RF is a ratio so it is invariant
    under scaling, but the debug factors must reflect the real image."""
    plate_in_media(width=400, height=400)
    response = post(client, rf_payload(canvasWidth=200, canvasHeight=200))

    body = response.json()
    assert body['rf_values'][0]['rf'] == 0.5
    assert body['debug_info']['original_image_size'] == '400x400'
    assert body['debug_info']['scaling_factors'] == 'x=2.00, y=2.00'


@pytest.mark.django_db
def test_local_image_via_base64(client, png_bytes, rf_payload):
    """The Upload Local path sends the image inline rather than by name."""
    encoded = base64.b64encode(png_bytes()).decode()
    response = post(client, rf_payload(
        isLocalImage=True,
        imageData=f'data:image/png;base64,{encoded}',
        imageSrc='local_upload.png',
    ))

    assert response.status_code == 200
    assert response.json()['rf_values'][0]['rf'] == 0.5


@pytest.mark.django_db
def test_outputs_are_written_and_csv_matches_response(client, plate_in_media, rf_payload, media_root):
    """The annotated PNG and the CSV both land under MEDIA_ROOT, and the CSV
    carries the same values the JSON reports."""
    plate_in_media()
    body = post(client, rf_payload()).json()

    annotated = media_root / body['annotated_image_url'].removeprefix('/media/')
    csv_file = media_root / body['csv_url'].removeprefix('/media/')
    assert annotated.is_file() and annotated.stat().st_size > 0
    assert csv_file.is_file()

    rows = list(csv.DictReader(csv_file.read_text().splitlines()))
    assert [row['RF value'] for row in rows] == ['0.5']
    assert list(rows[0]) == ['Solvent (X value)', 'Compound (Y value)', 'RF value']


@pytest.mark.django_db
def test_missing_image_reported_not_crashed(client, rf_payload):
    response = post(client, rf_payload(imageSrc='does-not-exist.png'))
    assert response.status_code == 400
    assert 'error' in response.json()

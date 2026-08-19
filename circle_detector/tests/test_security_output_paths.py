"""calculate_rf must not let imageSrc steer where files are written.

imageSrc was interpolated into the output filenames via os.path.splitext and
joined onto MEDIA_ROOT, so ../../tmp/evil escaped MEDIA_ROOT.
"""
import json

import pytest

URL = '/calculate_rf/'


def post(client, payload):
    return client.post(URL, data=json.dumps(payload), content_type='application/json')


@pytest.mark.django_db
@pytest.mark.parametrize('hostile', [
    '../../tmp/pwned',
    '../../../tmp/pwned.png',
    '/tmp/pwned.png',
    '....//....//tmp/pwned',
])
def test_outputs_stay_inside_media_root(client, png_bytes, rf_payload, media_root, tmp_path, hostile):
    """Sent as a local image so the hostile value reaches the filename builder
    without having to name a readable file."""
    import base64
    encoded = base64.b64encode(png_bytes()).decode()

    response = post(client, rf_payload(
        isLocalImage=True,
        imageData=f'data:image/png;base64,{encoded}',
        imageSrc=hostile,
    ))

    assert response.status_code in (200, 400)
    escaped = [p for p in tmp_path.rglob('*pwned*') if media_root not in p.parents]
    assert escaped == []

    if response.status_code == 200:
        body = response.json()
        for url in (body['annotated_image_url'], body['csv_url']):
            assert '..' not in url
            assert (media_root / url.removeprefix('/media/')).is_file()


@pytest.mark.django_db
def test_missing_image_src_with_local_path_does_not_500(client, png_bytes, media_root, rf_payload):
    """imageSrc=None passed the guard and then hit os.path.splitext(None),
    raising TypeError. Unreachable from the UI, reachable from any API client.
    """
    (media_root / 'local_uploads').mkdir()
    (media_root / 'local_uploads' / 'a.png').write_bytes(png_bytes())

    response = post(client, rf_payload(
        isLocalImage=True,
        imageSrc=None,
        localImagePath='local_uploads/a.png',
    ))

    assert response.status_code != 500
    assert response.status_code == 200


@pytest.mark.django_db
def test_local_image_path_cannot_escape_media_root(client, rf_payload, tmp_path):
    """localImagePath was already containment-checked; this pins that."""
    secret = tmp_path / 'secret.png'
    secret.write_bytes(b'not really a png')

    response = post(client, rf_payload(
        isLocalImage=True,
        imageSrc='x.png',
        localImagePath='../secret.png',
    ))

    assert response.status_code == 400


@pytest.mark.django_db
def test_image_src_cannot_read_outside_media_root(client, rf_payload):
    """The read side of the same field: it was joined onto MEDIA_ROOT with no
    containment check."""
    response = post(client, rf_payload(imageSrc='../../etc/passwd'))
    assert response.status_code == 400


@pytest.mark.django_db
def test_concurrent_calculations_do_not_overwrite(client, plate_in_media, rf_payload):
    """int(time.time()) is second-granular, so two requests in the same second
    produced the same output filename."""
    plate_in_media()
    first = post(client, rf_payload()).json()
    second = post(client, rf_payload()).json()

    assert first['annotated_image_url'] != second['annotated_image_url']
    assert first['csv_url'] != second['csv_url']

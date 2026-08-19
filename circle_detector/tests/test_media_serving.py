"""Annotated images and CSVs must be reachable in production.

Media was only routed when DEBUG=True, so with DEBUG=False the URLs
calculate_rf returned both 404'd and the app's primary output was
unreachable. Django forces DEBUG=False under test, which is exactly the
condition that was broken.
"""
import json

import pytest
from django.conf import settings

RF_URL = '/calculate_rf/'


def read(response):
    return b''.join(response.streaming_content) if response.streaming else response.content


@pytest.mark.django_db
def test_media_is_served_with_debug_off(client, plate_in_media, rf_payload):
    assert settings.DEBUG is False, 'the bug only appeared with DEBUG off'
    plate_in_media()

    body = client.post(RF_URL, data=json.dumps(rf_payload()), content_type='application/json').json()

    annotated = client.get(body['annotated_image_url'])
    assert annotated.status_code == 200
    assert annotated['Content-Type'] == 'image/png'
    assert read(annotated).startswith(b'\x89PNG')


@pytest.mark.django_db
def test_csv_is_served_as_an_attachment(client, plate_in_media, rf_payload):
    plate_in_media()
    body = client.post(RF_URL, data=json.dumps(rf_payload()), content_type='application/json').json()

    response = client.get(body['csv_url'])
    assert response.status_code == 200
    assert 'attachment' in response['Content-Disposition']
    assert b'RF value' in read(response)


@pytest.mark.django_db
@pytest.mark.parametrize('attempt', [
    '/media/../rf_calculator/settings.py',
    '/media/../../etc/passwd',
])
def test_media_route_refuses_traversal(client, attempt):
    response = client.get(attempt)
    assert response.status_code in (400, 404)
    assert b'SECRET_KEY' not in read(response)


@pytest.mark.django_db
def test_media_route_refuses_dangerous_extensions(client, media_root):
    """Defence in depth behind the upload fix: even if an .html file reaches
    MEDIA_ROOT, the media route must not hand it back as a rendered page."""
    (media_root / 'evil.html').write_text('<script>alert(1)</script>')

    response = client.get('/media/evil.html')

    assert response.status_code == 404 or 'text/html' not in response.get('Content-Type', '')


@pytest.mark.django_db
def test_media_responses_carry_hardening_headers(client, plate_in_media):
    plate_in_media('plate.png')
    response = client.get('/media/plate.png')

    assert response.status_code == 200
    assert response['X-Content-Type-Options'] == 'nosniff'
    assert 'sandbox' in response['Content-Security-Policy']


@pytest.mark.django_db
def test_missing_media_is_a_clean_404(client):
    assert client.get('/media/nope.png').status_code == 404

"""Uploads must derive their extension from the bytes, not the filename.

The extension used to come from the client-supplied filename with only the
client-supplied Content-Type validated, so evil.html declared as image/png
was stored as <uuid>.html. Harmless while media 404'd; stored XSS as soon as
/media/ is served.
"""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

URL = '/upload-local-image/'


@pytest.mark.django_db
def test_html_masquerading_as_png_is_rejected(client, media_root):
    payload = b'<html><script>alert(document.domain)</script></html>'
    upload = SimpleUploadedFile('evil.html', payload, content_type='image/png')

    response = client.post(URL, {'image': upload})

    assert response.status_code == 400
    assert list(media_root.rglob('*.html')) == []


@pytest.mark.django_db
def test_extension_comes_from_content_not_filename(client, png_bytes, media_root):
    """Real PNG bytes with a hostile name must be stored as .png."""
    upload = SimpleUploadedFile('evil.html', png_bytes(), content_type='text/html')

    response = client.post(URL, {'image': upload})

    assert response.status_code == 200
    assert response.json()['imagePath'].endswith('.png')
    assert list(media_root.rglob('*.html')) == []


@pytest.mark.django_db
def test_stored_path_is_media_relative(client, png_bytes):
    """imagePath is posted back as localImagePath, so it must be relative."""
    upload = SimpleUploadedFile('plate.png', png_bytes(), content_type='image/png')

    path = client.post(URL, {'image': upload}).json()['imagePath']

    assert not path.startswith('/')
    assert path.startswith('local_uploads/')


@pytest.mark.django_db
def test_filename_cannot_escape_the_upload_directory(client, png_bytes, media_root):
    upload = SimpleUploadedFile('../../escaped.png', png_bytes(), content_type='image/png')

    response = client.post(URL, {'image': upload})

    assert response.status_code == 200
    stored = media_root / response.json()['imagePath']
    assert stored.resolve().is_relative_to(media_root.resolve())


@pytest.mark.django_db
def test_rejects_empty_and_missing_uploads(client):
    assert client.post(URL, {}).status_code == 400
    empty = SimpleUploadedFile('x.png', b'', content_type='image/png')
    assert client.post(URL, {'image': empty}).status_code == 400


@pytest.mark.django_db
def test_get_not_allowed(client):
    assert client.get(URL).status_code == 405

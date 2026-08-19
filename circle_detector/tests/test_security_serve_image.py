"""/serve-image/ must only serve files contained in MEDIA_ROOT.

Before hardening, file_path was passed straight to open(): a live
unauthenticated arbitrary file read, confirmed against a running container
with /etc/passwd.
"""
import pytest

URL = '/serve-image/'


@pytest.mark.django_db
@pytest.mark.parametrize('attempt', [
    '/etc/passwd',
    '/etc/hostname',
    '/code/rf_calculator/settings.py',
    '../rf_calculator/settings.py',
    '../../etc/passwd',
    'local_uploads/../../../etc/passwd',
])
def test_traversal_and_absolute_paths_are_refused(client, attempt):
    response = client.get(URL, {'file_path': attempt})
    assert response.status_code == 404
    body = b''.join(response.streaming_content) if response.streaming else response.content
    assert b'root:x:' not in body
    assert b'SECRET_KEY' not in body


@pytest.mark.django_db
def test_serves_a_contained_image(client, plate_in_media):
    plate_in_media('plate.png')
    response = client.get(URL, {'file_path': 'plate.png'})

    assert response.status_code == 200
    assert response['Content-Type'] == 'image/png'


@pytest.mark.django_db
def test_never_redirects(client, plate_in_media):
    """annotate.html re-derives file_path out of response.url, so a redirect
    would make it read null and the plate would silently fail to load."""
    plate_in_media('plate.png')
    response = client.get(URL, {'file_path': 'plate.png'})
    assert response.status_code not in (301, 302, 303, 307, 308)


@pytest.mark.django_db
@pytest.mark.parametrize('params', [{}, {'file_path': ''}])
def test_missing_path_is_a_clean_404(client, params):
    """Not a 500: file_path was previously handed to os.path.exists(None)."""
    assert client.get(URL, params).status_code == 404

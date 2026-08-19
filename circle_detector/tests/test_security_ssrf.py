"""/fetch-image/ must not be usable as a proxy into the internal network.

Unauthenticated, no scheme or host check, no timeout, no size cap before
hardening. The redirect cases matter most: validating only the first URL is
the classic bypass.
"""
from unittest import mock

import pytest

from circle_detector.remote import RemoteFetchError, validate_remote_url

URL = '/fetch-image/'


def resolves_to(*addresses):
    """Pin DNS so tests never depend on the network or on real hostnames."""
    return mock.patch(
        'circle_detector.remote.socket.getaddrinfo',
        side_effect=lambda host, port, **kw: [
            (2, 1, 6, '', (address, port)) for address in addresses
        ],
    )


class TestValidateRemoteUrl:
    @pytest.mark.parametrize('address', [
        '127.0.0.1',        # loopback
        '10.0.0.5',         # private
        '192.168.1.1',      # private
        '172.16.0.1',       # private
        '169.254.169.254',  # cloud metadata
        '100.64.0.1',       # CGNAT
        '0.0.0.0',          # unspecified
        '224.0.0.1',        # multicast
        '::1',              # loopback v6
        'fc00::1',          # unique local v6
    ])
    def test_rejects_non_global_addresses(self, address):
        with resolves_to(address):
            with pytest.raises(RemoteFetchError):
                validate_remote_url('http://eln.example.org/plate.png')

    def test_rejects_when_any_resolved_address_is_internal(self):
        """A hostname resolving to both a public and an internal address must
        be refused outright, not connected to on the public one."""
        with resolves_to('93.184.216.34', '127.0.0.1'):
            with pytest.raises(RemoteFetchError):
                validate_remote_url('http://eln.example.org/plate.png')

    def test_accepts_a_public_address(self):
        with resolves_to('93.184.216.34'):
            assert validate_remote_url('https://eln.example.org/plate.png')

    @pytest.mark.parametrize('url', [
        'file:///etc/passwd',
        'gopher://internal/',
        'ftp://internal/x.png',
        'data:image/png;base64,AAAA',
        'http://',
        'not-a-url',
        '',
        None,
    ])
    def test_rejects_non_http_schemes_and_junk(self, url):
        with pytest.raises(RemoteFetchError):
            validate_remote_url(url)

    def test_allowlist_restricts_to_named_hosts(self, settings):
        settings.ELN_ALLOWED_HOSTS = ['eln.example.org']
        assert validate_remote_url('https://eln.example.org/x.png')
        with pytest.raises(RemoteFetchError):
            validate_remote_url('https://evil.example.com/x.png')

    def test_allowlist_skips_dns_resolution(self, settings):
        """With an allowlist the host is trusted by name, which is also what
        makes the DNS-rebinding residual risk irrelevant in production."""
        settings.ELN_ALLOWED_HOSTS = ['internal-eln']
        with mock.patch('circle_detector.remote.socket.getaddrinfo') as resolver:
            assert validate_remote_url('http://internal-eln/x.png')
        resolver.assert_not_called()


@pytest.mark.django_db
class TestFetchImageEndpoint:
    @pytest.mark.parametrize('url', [
        'http://169.254.169.254/latest/meta-data/',
        'http://127.0.0.1:8050/',
        'http://10.0.0.1/',
        'file:///etc/passwd',
    ])
    def test_internal_targets_are_refused(self, client, url):
        response = client.get(URL, {'url': url})
        assert response.status_code == 400
        assert 'imagePath' not in response.json()

    def test_missing_url_is_rejected(self, client):
        assert client.get(URL, {}).status_code == 400

    def test_post_not_allowed(self, client):
        """405 from require_GET here. In the running app CSRF middleware
        rejects it first with 403, since this view is not csrf_exempt; the test
        client disables CSRF enforcement. Either way it is refused."""
        assert client.post(URL, {'url': 'https://example.org/x.png'}).status_code in (403, 405)

    def test_redirect_to_an_internal_address_is_refused(self, client, png_bytes):
        """Every hop is revalidated. Validating only the first URL is the bug
        this test exists for."""
        first = mock.Mock(status_code=302, is_redirect=True, is_permanent_redirect=False,
                          headers={'Location': 'http://169.254.169.254/'})
        with resolves_to('93.184.216.34'), \
             mock.patch('circle_detector.remote.requests.get', return_value=first):
            response = client.get(URL, {'url': 'https://eln.example.org/plate.png'})

        assert response.status_code == 400

    def test_oversized_response_is_aborted(self, client, settings):
        settings.ELN_FETCH_MAX_BYTES = 1024
        oversized = mock.Mock(
            status_code=200, is_redirect=False, is_permanent_redirect=False,
            headers={'Content-Type': 'image/png'},
        )
        oversized.iter_content.return_value = iter([b'x' * 512] * 10)
        with resolves_to('93.184.216.34'), \
             mock.patch('circle_detector.remote.requests.get', return_value=oversized):
            response = client.get(URL, {'url': 'https://eln.example.org/plate.png'})

        assert response.status_code == 400

    def test_non_image_content_is_refused(self, client):
        html = mock.Mock(
            status_code=200, is_redirect=False, is_permanent_redirect=False,
            headers={'Content-Type': 'text/html'},
        )
        html.iter_content.return_value = iter([b'<html></html>'])
        with resolves_to('93.184.216.34'), \
             mock.patch('circle_detector.remote.requests.get', return_value=html):
            response = client.get(URL, {'url': 'https://eln.example.org/evil'})

        assert response.status_code == 400

    def test_successful_fetch_stores_flat_and_relative(self, client, png_bytes, media_root):
        """The stored name must be flat in MEDIA_ROOT and returned relative:
        annotate.html reduces it to a basename before posting it back, so a
        subdirectory would silently break calculate_rf."""
        payload = png_bytes()
        ok = mock.Mock(
            status_code=200, is_redirect=False, is_permanent_redirect=False,
            headers={'Content-Type': 'image/png'},
        )
        ok.iter_content.return_value = iter([payload])
        with resolves_to('93.184.216.34'), \
             mock.patch('circle_detector.remote.requests.get', return_value=ok):
            body = client.get(URL, {'url': 'https://eln.example.org/plate.png'}).json()

        path = body['imagePath']
        assert '/' not in path
        assert not path.startswith('/')
        assert (media_root / path).is_file()

    def test_concurrent_fetches_do_not_collide(self, client, png_bytes, media_root):
        """Every download used to be written to the single fixed filename
        downloaded_image.png, so two users overwrote each other."""
        def fresh_response(*args, **kwargs):
            response = mock.Mock(
                status_code=200, is_redirect=False, is_permanent_redirect=False,
                headers={'Content-Type': 'image/png'},
            )
            response.iter_content.return_value = iter([png_bytes()])
            return response

        with resolves_to('93.184.216.34'), \
             mock.patch('circle_detector.remote.requests.get', side_effect=fresh_response):
            first = client.get(URL, {'url': 'https://eln.example.org/a.png'}).json()
            second = client.get(URL, {'url': 'https://eln.example.org/b.png'}).json()

        assert first['imagePath'] != second['imagePath']
        assert (media_root / first['imagePath']).is_file()
        assert (media_root / second['imagePath']).is_file()

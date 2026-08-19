"""The annotation page itself: it must render, and its responses must carry
the hardening headers that make the vendored-asset work worth doing."""
import json
import re

import pytest


@pytest.mark.django_db
def test_page_renders(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'TLC Image Annotation Tool' in response.content


@pytest.mark.django_db
def test_page_references_only_local_assets(client):
    """The rendered output, not just the template source: {% static %} must
    resolve to same-origin URLs."""
    html = client.get('/').content.decode()

    for url in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html):
        assert not url.startswith('http'), f'external asset in rendered page: {url}'
        assert not url.startswith('//'), f'protocol-relative asset: {url}'


@pytest.mark.django_db
def test_csp_forbids_external_origins(client):
    policy = client.get('/')['Content-Security-Policy']

    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


@pytest.mark.django_db
def test_eln_allowlist_is_rendered_for_the_page(client, settings):
    """The page needs the allowlist to refuse uploading results to a host the
    deployment has not named."""
    settings.ELN_ALLOWED_HOSTS = ['eln.example.org']
    html = client.get('/').content.decode()

    match = re.search(r'id="eln-allowed-hosts"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert match, 'the allowlist json_script block is missing'
    assert json.loads(match.group(1)) == ['eln.example.org']


@pytest.mark.django_db
def test_empty_allowlist_renders_as_an_empty_array(client, settings):
    settings.ELN_ALLOWED_HOSTS = []
    html = client.get('/').content.decode()

    match = re.search(r'id="eln-allowed-hosts"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert json.loads(match.group(1)) == []


@pytest.mark.django_db
def test_no_csrf_token_is_sent_to_the_external_eln(client):
    """The token is useless to a third party and leaks as soon as CSRF
    protection is enabled. Both upload paths must post without it."""
    html = client.get('/').content.decode()

    for block in re.findall(r'fetch\(externalApiUrl,\s*\{.*?\}\)', html, re.DOTALL):
        assert 'X-CSRFToken' not in block


@pytest.mark.django_db
def test_the_duplicate_eln_loader_is_gone(client):
    html = client.get('/').content.decode()
    assert 'loadAndRenderImage' not in html
    assert html.count('/fetch-image/') == 1, 'the ELN image should be fetched once'

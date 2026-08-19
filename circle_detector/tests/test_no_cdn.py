"""The UI must not depend on external hosts.

The page used to pull Materialize, SweetAlert2 and Google Fonts from CDNs, all
called unguarded, so the app broke on an air-gapped network and could not run
under a restrictive CSP.
"""
import re
from pathlib import Path

import pytest
from django.conf import settings

TEMPLATES = sorted(Path(settings.BASE_DIR).glob('circle_detector/templates/**/*.html'))
EXTERNAL = re.compile(r'''(?:src|href)\s*=\s*["'](https?:)?//''', re.IGNORECASE)


def test_templates_were_found():
    assert TEMPLATES, 'template glob matched nothing; the guard below would be vacuous'


@pytest.mark.parametrize('template', TEMPLATES, ids=lambda p: p.name)
def test_no_external_asset_references(template):
    hits = EXTERNAL.findall(template.read_text())
    assert not hits, f'{template.name} still references an external asset host'


@pytest.mark.parametrize('template', TEMPLATES, ids=lambda p: p.name)
def test_no_remote_font_urls_in_css(template):
    assert 'fonts.gstatic.com' not in template.read_text()
    assert 'fonts.googleapis.com' not in template.read_text()


def test_vendored_assets_are_present():
    vendor = Path(settings.BASE_DIR) / 'circle_detector/static/circle_detector/vendor'
    expected = {
        'materialize.min.css',
        'materialize.min.js',
        'sweetalert2.all.min.js',
        'material-icons.css',
        'inter.css',
    }
    present = {p.name for p in vendor.rglob('*')}
    assert expected <= present, f'missing vendored assets: {expected - present}'


def test_vendored_css_references_only_local_fonts():
    vendor = Path(settings.BASE_DIR) / 'circle_detector/static/circle_detector/vendor'
    for stylesheet in vendor.rglob('*.css'):
        for url in re.findall(r'url\(([^)]+)\)', stylesheet.read_text()):
            assert '//' not in url, f'{stylesheet.name} points at a remote font: {url}'


def test_licences_are_recorded():
    vendor = Path(settings.BASE_DIR) / 'circle_detector/static/circle_detector/vendor'
    licences = (vendor / 'LICENSES.md').read_text()
    for project in ['Materialize', 'SweetAlert2', 'Inter', 'Material Icons']:
        assert project in licences

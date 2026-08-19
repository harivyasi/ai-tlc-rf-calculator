"""Shared fixtures.

Every test gets its own MEDIA_ROOT under tmp_path: the views write annotated
images, CSVs and uploads to disk, and no test may leave anything in the repo.
"""
import io

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    """Redirect MEDIA_ROOT at every test. autouse: opting in per-test is how
    stray files end up in the working tree."""
    root = tmp_path / 'media'
    root.mkdir()
    settings.MEDIA_ROOT = str(root)
    return root


@pytest.fixture
def png_bytes():
    """Factory for real PNG bytes. Generated rather than committed as a binary
    fixture so the plate size can vary per test."""
    def _make(width=200, height=200, colour=(230, 230, 230)):
        buffer = io.BytesIO()
        Image.new('RGB', (width, height), colour).save(buffer, format='PNG')
        return buffer.getvalue()
    return _make


@pytest.fixture
def plate_in_media(media_root, png_bytes):
    """Write a plate into MEDIA_ROOT and return its media-relative name, which
    is what the frontend posts as imageSrc."""
    def _make(name='plate.png', width=200, height=200):
        (media_root / name).write_bytes(png_bytes(width, height))
        return name
    return _make


@pytest.fixture
def rf_payload():
    """The calculate_rf body the frontend sends.

    Defaults are the golden case: on a 200x200 plate with the baseline at
    y=180 and the solvent front at y=20, a spot at y=100 has travelled 80 of
    the front's 160, so RF = 0.5.
    """
    def _make(**overrides):
        payload = {
            'lines': [{'type': 'min', 'y': 180}, {'type': 'max', 'y': 20}],
            'spots': [{'x': 100, 'y': 100}],
            'imageSrc': 'plate.png',
            'isLocalImage': False,
            'canvasWidth': 200,
            'canvasHeight': 200,
            'scaleFactor': 1,
        }
        payload.update(overrides)
        return payload
    return _make

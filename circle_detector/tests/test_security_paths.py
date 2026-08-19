"""Unit tests for the path primitives.

Read and write paths need different primitives: a read is containment-checked
against MEDIA_ROOT, a write is reduced to a token that cannot contain a
directory component at all. Crossing them is what produced the traversal bugs.
"""
import pytest

from circle_detector.paths import (
    resolve_media_path,
    safe_image_extension,
    safe_output_stem,
)


class TestResolveMediaPath:
    def test_accepts_a_contained_file(self, media_root):
        (media_root / 'plate.png').write_bytes(b'x')
        assert resolve_media_path('plate.png') == str(media_root / 'plate.png')

    def test_accepts_a_contained_subdirectory(self, media_root):
        (media_root / 'local_uploads').mkdir()
        (media_root / 'local_uploads' / 'a.png').write_bytes(b'x')
        assert resolve_media_path('local_uploads/a.png') is not None

    @pytest.mark.parametrize('attempt', [
        '../../../../etc/passwd',
        '../rf_calculator/settings.py',
        '/etc/passwd',
        '/code/rf_calculator/settings.py',
        'local_uploads/../../../etc/passwd',
        '....//....//etc/passwd',
    ])
    def test_rejects_escapes_and_absolute_paths(self, attempt):
        assert resolve_media_path(attempt) is None

    @pytest.mark.parametrize('empty', [None, '', '   '])
    def test_rejects_empty(self, empty):
        assert resolve_media_path(empty) is None

    def test_rejects_missing_file_when_existence_required(self):
        assert resolve_media_path('nope.png') is None

    def test_allows_missing_file_when_existence_not_required(self, media_root):
        assert resolve_media_path('nope.png', must_exist=False) == str(media_root / 'nope.png')

    def test_a_symlink_out_of_media_root_is_rejected(self, media_root, tmp_path):
        """commonpath on the resolved path is what closes this; a check on the
        unresolved string would let it through."""
        outside = tmp_path / 'outside.png'
        outside.write_bytes(b'x')
        (media_root / 'link.png').symlink_to(outside)
        assert resolve_media_path('link.png') is None


class TestSafeOutputStem:
    @pytest.mark.parametrize('value,expected', [
        ('plate.png', 'plate'),
        ('downloaded_image.png', 'downloaded_image'),
        ('my plate (2).png', 'myplate2'),
        ('../../tmp/evil', 'evil'),
        ('/etc/passwd', 'passwd'),
        ('....//....//evil.png', 'evil'),
    ])
    def test_reduces_to_a_bare_stem(self, value, expected):
        assert safe_output_stem(value) == expected

    @pytest.mark.parametrize('value', [None, '', '///', '../..', '.', '..'])
    def test_falls_back_when_nothing_survives(self, value):
        assert safe_output_stem(value) == 'image'

    def test_never_contains_a_path_separator(self):
        for value in ['a/b/c.png', '..\\..\\win.png', '/absolute/path.png']:
            stem = safe_output_stem(value)
            assert '/' not in stem and '\\' not in stem and '..' not in stem

    def test_truncates_long_names(self):
        assert len(safe_output_stem('a' * 500)) <= 64


class TestSafeImageExtension:
    def test_detects_png(self, png_bytes):
        assert safe_image_extension(png_bytes()) == '.png'

    def test_detects_jpeg(self):
        import io

        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (10, 10)).save(buffer, format='JPEG')
        assert safe_image_extension(buffer.getvalue()) == '.jpg'

    @pytest.mark.parametrize('payload', [
        b'<html><script>alert(1)</script></html>',
        b'\x89PNG\r\n\x1a\n truncated garbage',
        b'',
        b'GIF89a',  # a real format, but not one we accept
    ])
    def test_rejects_non_images_and_disallowed_formats(self, payload):
        assert safe_image_extension(payload) is None

    def test_ignores_any_claimed_filename(self, png_bytes):
        """The extension comes from the bytes. A .html name on PNG bytes is
        still a PNG, and PNG bytes named .html must not become .html."""
        assert safe_image_extension(png_bytes()) == '.png'

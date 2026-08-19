"""Deployment defaults must be safe without an operator remembering flags."""
import importlib
import os
import re
import secrets
from unittest import mock

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def reload_settings(**env):
    """Re-import the settings module under a given environment."""
    import rf_calculator.settings as module
    with mock.patch.dict(os.environ, env, clear=False):
        return importlib.reload(module)


def test_debug_defaults_to_false():
    with mock.patch.dict(os.environ, {}, clear=True):
        module = reload_settings(SECRET_KEY='x' * 50)
    assert module.DEBUG is False


def test_secret_key_is_required_when_debug_is_off():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ImproperlyConfigured):
            reload_settings(DEBUG='False')


def test_secret_key_is_generated_in_debug():
    """Local runserver must work with no setup, but the key must not be the
    literal that was committed to git."""
    with mock.patch.dict(os.environ, {}, clear=True):
        module = reload_settings(DEBUG='True')
    assert module.SECRET_KEY
    assert 'django-insecure-6^m!9iw' not in module.SECRET_KEY


def test_allowed_hosts_defaults_to_wildcard():
    module = reload_settings(SECRET_KEY='x' * 50)
    assert module.ALLOWED_HOSTS == ['*']


def test_allowed_hosts_is_env_overridable():
    module = reload_settings(SECRET_KEY='x' * 50, ALLOWED_HOSTS='a.example.org,b.example.org')
    assert module.ALLOWED_HOSTS == ['a.example.org', 'b.example.org']


def test_no_hardcoded_lab_address():
    from pathlib import Path
    source = (Path(settings.BASE_DIR) / 'rf_calculator/settings.py').read_text()
    assert '172.22.211.68' not in source


def test_whitenoise_middleware_is_installed():
    """The storage backend was configured without the middleware, which is why
    /static/ 404'd in production."""
    assert 'whitenoise.middleware.WhiteNoiseMiddleware' in settings.MIDDLEWARE
    security = settings.MIDDLEWARE.index('django.middleware.security.SecurityMiddleware')
    whitenoise = settings.MIDDLEWARE.index('whitenoise.middleware.WhiteNoiseMiddleware')
    assert whitenoise == security + 1


def test_staticfiles_storage_uses_the_modern_setting_only():
    """Setting both STORAGES and STATICFILES_STORAGE is fatal in Django 4.2."""
    from pathlib import Path
    source = (Path(settings.BASE_DIR) / 'rf_calculator/settings.py').read_text()
    assert re.search(r'^STORAGES\s*=', source, re.MULTILINE)
    # Assignment, not any mention: the comment above STORAGES names the old
    # setting to explain why it is gone.
    assert not re.search(r'^STATICFILES_STORAGE\s*=', source, re.MULTILINE)


def test_deprecated_use_l10n_is_gone():
    from pathlib import Path
    source = (Path(settings.BASE_DIR) / 'rf_calculator/settings.py').read_text()
    assert not re.search(r'^USE_L10N\s*=', source, re.MULTILINE)


def test_proxy_ssl_header_is_off_unless_requested():
    """Trusting X-Forwarded-Proto with no proxy in front is spoofable."""
    module = reload_settings(SECRET_KEY='x' * 50)
    assert getattr(module, 'SECURE_PROXY_SSL_HEADER', None) is None

    module = reload_settings(SECRET_KEY='x' * 50, TRUST_PROXY_SSL_HEADER='True')
    assert module.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https')


def test_deploy_checks_pass_with_tls_enabled():
    from django.core.management import call_command
    from io import StringIO

    with mock.patch.multiple(
        settings,
        SECRET_KEY=secrets.token_urlsafe(64),
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
        SECURE_HSTS_PRELOAD=True,
        SECURE_SSL_REDIRECT=True,
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
        DEBUG=False,
    ):
        out = StringIO()
        call_command('check', '--deploy', stdout=out, stderr=out)
        assert 'System check identified no issues' in out.getvalue()


def test_admin_is_disabled_by_default(client):
    """No models and no registered admin, so it is pure login surface."""
    assert client.get('/admin/').status_code == 404


def test_admin_app_stays_installed_for_collectstatic():
    assert 'django.contrib.admin' in settings.INSTALLED_APPS

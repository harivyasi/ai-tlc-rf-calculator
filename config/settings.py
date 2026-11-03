"""Django settings for the TLC RF Calculator project.

Runtime configuration is driven by environment variables so the same image can
run in development and production:

    DEBUG                  Enable debug mode (default: False).
    SECRET_KEY             Django secret key (required when DEBUG is off).
    ALLOWED_HOSTS          Comma-separated allowed hosts.
    ELN_ORIGIN             The ELN origin (e.g. https://eln.example.org). Sets
                           both the CSP connect-src and the fetch allowlist.

A minimal deployment needs only SECRET_KEY, ALLOWED_HOSTS, and (for ELN
save-back) ELN_ORIGIN. Advanced overrides — CSRF_TRUSTED_ORIGINS,
CSP_CONNECT_SRC, ELN_ALLOWED_HOSTS, MAX_UPLOAD_MB, SECURE_* — are all optional
and default sensibly from the three above.

See https://docs.djangoproject.com/en/5.2/ref/settings/
"""

import os
from pathlib import Path

from .env import csrf_origins_from_hosts, env_bool, env_int, env_list, host_of

# A single ELN_ORIGIN (e.g. https://eln.example.org) configures both the CSP
# connect-src (so the browser may POST results back) and the remote-fetch
# allowlist below — one variable instead of two. Either derived value can still
# be overridden explicitly (CSP_CONNECT_SRC / ELN_ALLOWED_HOSTS).
ELN_ORIGIN = os.getenv("ELN_ORIGIN", "").strip()
ELN_ORIGIN_HOST = host_of(ELN_ORIGIN)

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# DEBUG is off by default; enable it explicitly for local development.
DEBUG = env_bool("DEBUG", False)

# The secret key must be provided via the environment in any real deployment.
# A throwaway key is generated only when running with DEBUG enabled.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        from django.core.management.utils import get_random_secret_key

        SECRET_KEY = get_random_secret_key()
    else:
        raise RuntimeError("The SECRET_KEY environment variable must be set when DEBUG is off.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

# Trusted CSRF origins default to https://<host> for each real ALLOWED_HOSTS
# entry (local / wildcard hosts are skipped), so you rarely need to set this
# separately. An explicit CSRF_TRUSTED_ORIGINS overrides the derivation.
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS") or csrf_origins_from_hosts(ALLOWED_HOSTS)

# Hosts the server may fetch ELN images from (SSRF allowlist). Empty = any
# public host. Explicit ELN_ALLOWED_HOSTS wins, else the ELN_ORIGIN host.
ELN_ALLOWED_HOSTS = env_list("ELN_ALLOWED_HOSTS") or ([ELN_ORIGIN_HOST] if ELN_ORIGIN_HOST else [])


# Application definition

# This app has no database models and no admin/auth needs; CSRF protection is
# cookie-based, so the auth / sessions / messages / admin apps are intentionally
# omitted to minimise the attack surface.
INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "tlc",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "config.middleware.ContentSecurityPolicyMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Content-Security-Policy. The frontend is fully self-hosted with no inline
# scripts, inline styles, or inline event handlers, so scripts and styles are
# locked to 'self'. Cross-origin fetches (e.g. saving results back to an ELN)
# are blocked unless their origin is allowed via ELN_ORIGIN (or CSP_CONNECT_SRC).
_csp_extra = env_list("CSP_CONNECT_SRC") or ([ELN_ORIGIN] if ELN_ORIGIN else [])
_csp_connect = " ".join(["'self'", *_csp_extra])
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        f"connect-src {_csp_connect}",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)

# Use WhiteNoise's compressed/hashed manifest storage only in production;
# in DEBUG, plain storage serves via finders so no collectstatic run is needed
# (e.g. when running the test suite).
_staticfiles_backend = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": _staticfiles_backend,
    },
}

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database: none. The app has no models; CSRF is cookie-based and there are no
# sessions/auth, so no database connection is configured. Results are written
# to MEDIA_ROOT as files, not persisted to a DB.
DATABASES: dict = {}


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Local uploads are sent as a base64 data URL inside the JSON request body,
# which inflates the payload ~33%. The frontend caps images at 10 MB, so the
# request body must be allowed to exceed Django's 2.5 MB default. Configurable
# via MAX_UPLOAD_MB (megabytes of request body).
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("MAX_UPLOAD_MB", 16) * 1024 * 1024


# Security hardening. These are enforced whenever DEBUG is off so that local
# development over plain HTTP keeps working, while deployments are secure.
# https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/
if not DEBUG:
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() in ("true", "1", "t")
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True
# The tool is opened as its own page (not embedded by the ELN), so deny all
# framing to fully block clickjacking.
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True

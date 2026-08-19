"""
Django settings for rf_calculator project.

Defaults are chosen to be safe when nothing is configured: DEBUG is off and a
missing SECRET_KEY is fatal rather than silently replaced. ALLOWED_HOSTS
defaults to '*' so the container runs on any host; validating the Host header
is then the reverse proxy's job, which is acceptable here because nothing in
this project builds an absolute URL from the request host.

For the deployment checklist, see
https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


def env_flag(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ('true', '1', 't', 'yes')


def env_list(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


DEBUG = env_flag('DEBUG', False)

# Never fall back to a literal committed to the repository. In DEBUG a
# throwaway key keeps `runserver` working with no setup; with DEBUG off an
# unset key is a configuration error we want to hear about at boot.
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = get_random_secret_key()
    else:
        raise ImproperlyConfigured(
            'SECRET_KEY must be set when DEBUG is off. '
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
        )

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS') or ['*']

# Django 4.x requires a scheme on each entry, e.g. https://eln.example.org.
# Note that ALLOWED_HOSTS='*' does NOT relax the CSRF origin check.
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')


# Application definition

INSTALLED_APPS = [
    # Kept installed even when the admin URL is disabled, so that
    # collectstatic still gathers the admin's own CSS and JS.
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'circle_detector',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Must follow SecurityMiddleware. Without it the WhiteNoise storage
    # backend below is inert and /static/ 404s whenever DEBUG is off.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'circle_detector.middleware.ContentSecurityPolicyMiddleware',
]

ROOT_URLCONF = 'rf_calculator.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'rf_calculator.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
# Unused by this project -- there are no models -- but Django requires an
# entry and the admin/session apps expect one to exist.

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.getenv('SQLITE_PATH', str(BASE_DIR / 'db.sqlite3')),
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static and media files
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('MEDIA_ROOT', os.path.join(BASE_DIR, 'media'))

# STORAGES replaces STATICFILES_STORAGE in Django 4.2; setting both raises
# ImproperlyConfigured. The non-manifest WhiteNoise backend is deliberate:
# manifest storage strictly validates every url() reference inside collected
# CSS and hard-fails collectstatic on a miss, which buys cache-busting this
# project does not need at the cost of a brittle build.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Security
# All TLS-dependent settings default to off so that a plain-HTTP deployment on
# an internal network still works; enable them per deployment.

SECURE_SSL_REDIRECT = env_flag('SECURE_SSL_REDIRECT', False)
SESSION_COOKIE_SECURE = env_flag('SESSION_COOKIE_SECURE', False)
CSRF_COOKIE_SECURE = env_flag('CSRF_COOKIE_SECURE', False)
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_flag('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = env_flag('SECURE_HSTS_PRELOAD', False)

# Only trust forwarded headers when a proxy is genuinely in front: otherwise
# any client can assert X-Forwarded-Proto: https and defeat the checks above.
if env_flag('TRUST_PROXY_SSL_HEADER', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Cap request bodies. Plate images arrive as base64 in JSON, so the data cap
# has to be generous, but it should not be unbounded.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('DATA_UPLOAD_MAX_MEMORY_SIZE', 40 * 1024 * 1024))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('FILE_UPLOAD_MAX_MEMORY_SIZE', 10 * 1024 * 1024))

ENABLE_ADMIN = env_flag('ENABLE_ADMIN', False)


# Project settings

SPOT_MODEL_PATH = BASE_DIR / 'circle_detector' / 'ml_models' / 'spot_best.onnx'
SPOT_IMGSZ_DEFAULT = 1280
SPOT_CONF_DEFAULT = 0.15
TLC_BAND_MARGIN = 12

LINE_MODEL_PATH = BASE_DIR / 'circle_detector' / 'ml_models' / 'line_best.onnx'
LINE_IMGSZ_DEFAULT = 1024
LINE_CONF_DEFAULT = 0.05

# Remote (ELN) image fetching. With hosts listed here, fetching is restricted
# to them by name; left empty, any public address is allowed but internal
# ranges are refused. See circle_detector/remote.py.
ELN_ALLOWED_HOSTS = env_list('ELN_ALLOWED_HOSTS')
ELN_FETCH_TIMEOUT = (
    float(os.getenv('ELN_CONNECT_TIMEOUT', '5')),
    float(os.getenv('ELN_READ_TIMEOUT', '20')),
)
ELN_FETCH_MAX_BYTES = int(os.getenv('ELN_FETCH_MAX_BYTES', 25 * 1024 * 1024))

# How long fetched ELN images are kept. They are written under unique names,
# so without a sweep an unauthenticated endpoint could fill the disk.
MEDIA_RETENTION_HOURS = float(os.getenv('MEDIA_RETENTION_HOURS', '24'))


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'standard'},
    },
    'root': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO')},
}

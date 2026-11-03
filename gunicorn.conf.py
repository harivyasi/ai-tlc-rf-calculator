"""Gunicorn configuration, driven by environment variables.

All values have sensible defaults but can be tuned per deployment, e.g.::

    GUNICORN_WORKERS=4 GUNICORN_TIMEOUT=120 gunicorn config.wsgi -c gunicorn.conf.py
"""

import os

bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8050")
workers = int(os.getenv("GUNICORN_WORKERS", os.getenv("WEB_CONCURRENCY", "3")))
threads = int(os.getenv("GUNICORN_THREADS", "2"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
# Log to stdout/stderr so a container platform collects them.
accesslog = "-"
errorlog = "-"

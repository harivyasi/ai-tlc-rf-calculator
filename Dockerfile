# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: resolve dependencies from the committed lockfile.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /code

# Lockfile only, so this layer is reused whenever application code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# No apt packages. opencv-python-headless bundles its own libraries and needs
# only libstdc++6 and zlib1g, both already in the slim image -- verified with
# readelf -d on the bundled cv2 .so. The libgl1/libsm6/libxext6/libxrender1
# block this image used to install is for non-headless OpenCV.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    MEDIA_ROOT=/code/media \
    # Cap the native thread pools. OpenBLAS (bundled in the cv2 wheel) and ONNX
    # Runtime each spawn one thread per core per session otherwise, which with
    # several request threads means hundreds of threads and a much larger RSS.
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    MKL_NUM_THREADS=2

WORKDIR /code

COPY --from=builder /opt/venv /opt/venv

# Application code, models last: the ~57MB of .onnx changes far less often
# than settings or views, so give it its own layer.
COPY manage.py ./
COPY rf_calculator/ ./rf_calculator/
COPY circle_detector/ ./circle_detector/

# SECRET_KEY has to be in this layer's environment, not merely declared as an
# ARG: settings.py refuses to import without one, and manage.py loads settings.
# --skip-checks avoids importing the URLconf (and, before models were made
# lazy, both ONNX sessions) purely to collect static files.
RUN SECRET_KEY=build-time-only-not-a-runtime-key \
    python manage.py collectstatic --noinput --skip-checks \
 && useradd --create-home --uid 10001 appuser \
 && mkdir -p /code/media \
 && chown -R appuser:appuser /code/media /code/staticfiles

USER appuser

EXPOSE 8050

# Mount a volume at /code/media: the annotated images and CSVs the UI links to
# are written there, so without one they vanish on restart.

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/', timeout=4)"

# gthread, not sync workers: one process holds a single copy of the ~430MB of
# ONNX sessions and serves concurrent requests on threads (session.run is
# thread-safe and the wrappers keep no per-request state). Deliberately no
# --preload: ONNX Runtime builds its thread pools when a session is created,
# and only the forking thread survives fork(), which shows up as an
# intermittent hang inside run() rather than a clean failure.
CMD ["gunicorn", "rf_calculator.wsgi:application", \
     "--bind", "0.0.0.0:8050", \
     "--worker-class", "gthread", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--max-requests", "500", \
     "--max-requests-jitter", "50", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]

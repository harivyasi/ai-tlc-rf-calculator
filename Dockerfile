# syntax=docker/dockerfile:1
FROM python:3.14-slim

# Faster, cleaner Python in containers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

# opencv-python-headless needs libglib2.0 at runtime (no OpenGL required).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install uv (copied from its official image).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /code

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Copy the project.
COPY . /code/

# Collect static files. A throwaway key is fine here; collectstatic reads no
# secrets. DEBUG stays off so production settings are exercised.
RUN SECRET_KEY=build-only-key python manage.py collectstatic --noinput

EXPOSE 8050

# SECRET_KEY, ALLOWED_HOSTS, etc. must be provided at runtime (see README).
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8050"]

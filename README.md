# TLC RF Calculator

A web tool for computing **retention factor (RF)** values from **Thin Layer
Chromatography (TLC)** plate images.

Upload a photo of a TLC plate (or load one from an Electronic Lab Notebook),
annotate the reference lines and spots in the browser, and the server computes
the RF for each spot, produces an annotated image, and exports the results as
CSV.

RF is defined as the distance travelled by a spot divided by the distance
travelled by the solvent front:

```text
RF = (spot_y − min_line_y) / (max_line_y − min_line_y)
```

## Features

- Interactive canvas annotation: min/max reference lines, vertical/horizontal
  label lines, and draggable spots.
- Two image sources: **local upload** or **fetch from an ELN URL**.
- Server-side RF calculation and image annotation (OpenCV).
- CSV export (download locally or POST back to an ELN).

## Tech stack

- **Django 5.2 LTS** (Python 3.12+)
- **OpenCV** (headless) + **NumPy** for image annotation
- **Gunicorn** + **WhiteNoise** for serving
- **uv** for dependency management
- **SQLite** (sessions/admin only; no domain models)

## Getting started

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install dependencies into a virtual environment
uv sync

# Run the development server (DEBUG generates a throwaway SECRET_KEY)
DEBUG=True uv run python manage.py migrate
DEBUG=True uv run python manage.py runserver
```

Then open <http://localhost:8000/>.

### Running the tests

Unit tests (RF calculation + endpoint security):

```bash
DEBUG=True uv run python manage.py test
```

End-to-end / regression tests (Playwright + Chromium):

```bash
uv run playwright install chromium   # one-time browser download
uv run pytest e2e -v
```

The E2E suite drives the annotation UI in a real browser. It also serves as a
**regression harness against the original frontend**: the pre-refactor
`annotate.html` is kept under `regression_legacy_templates/` and rendered via
`rf_calculator.settings_legacy`, so the same flow runs against both the old and
new frontends and asserts they produce identical RF results. Two further tests
document the fixed image-fetch bugs (no spurious fetch on load; the ELN path
fetches once instead of twice).

### Linting

```bash
uv run ruff check .
```

## Configuration

All configuration is via environment variables:

| Variable               | Default                     | Description                                                            |
| ---------------------- | --------------------------- | ---------------------------------------------------------------------- |
| `DEBUG`                | `False`                     | Enable Django debug mode. **Never enable in production.**              |
| `SECRET_KEY`           | *(required if not DEBUG)*   | Django secret key. A throwaway key is generated only when `DEBUG=True`. |
| `ALLOWED_HOSTS`        | `localhost,127.0.0.1`       | Comma-separated allowed hosts.                                         |
| `CSRF_TRUSTED_ORIGINS` | *(empty)*                   | Comma-separated trusted origins for CSRF (e.g. `https://tlc.example.org`). |
| `ELN_ALLOWED_HOSTS`    | *(empty = any public host)* | Comma-separated allowlist of hosts the server may fetch ELN images from. |
| `SECURE_SSL_REDIRECT`  | `True` (when not DEBUG)     | Redirect HTTP to HTTPS.                                                |
| `SECURE_HSTS_SECONDS`  | `31536000`                  | HSTS max-age (when not DEBUG).                                         |

When `DEBUG` is off, secure cookies, HSTS, and SSL redirect are enabled
automatically. Verify a deployment's configuration with:

```bash
uv run python manage.py check --deploy
```

## Security notes

- **Image fetching (`/fetch-image/`)** validates URLs against SSRF: only
  `http(s)` is allowed, the host must resolve to a public address, and an
  optional `ELN_ALLOWED_HOSTS` allowlist can restrict it further. Downloads are
  bounded by a timeout and a 25 MB size cap, and redirects are not followed.
- **Image serving (`/serve-image/`)** is confined to `MEDIA_ROOT`; path
  traversal is not possible.
- CSRF protection is enforced on the calculation endpoint.

## Deployment (Docker)

```bash
docker build -t tlc-rf-calculator .
docker run --rm -p 8050:8050 \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  -e ALLOWED_HOSTS="tlc.example.org" \
  -e CSRF_TRUSTED_ORIGINS="https://tlc.example.org" \
  tlc-rf-calculator
```

The app listens on port `8050`. Static files are served by WhiteNoise;
user-generated media is served by the app (fine for low-traffic internal use —
front it with a reverse proxy for higher load).

## Continuous integration

- **GitHub Actions** (`.github/workflows/ci.yml`): lints and tests on every
  push to `main` and pull request; builds and pushes the Docker image to the
  GitHub Container Registry (`ghcr.io`) only when a **release** is published.
- **GitLab CI** (`.gitlab-ci.yml`): lints and tests on branches / merge
  requests; builds and pushes the image to the GitLab registry only on a
  **tag** (release).

## Maintenance

Generated files (annotated images, CSVs, downloaded ELN images) accumulate in
`MEDIA_ROOT`. Prune old ones with the bundled management command — run it from
cron:

```bash
uv run python manage.py cleanup_media --hours 24        # delete files older than 24h
uv run python manage.py cleanup_media --hours 24 --dry-run
```

## Project layout

```text
rf_calculator/        Django project (settings, URLs, WSGI/ASGI)
circle_detector/      Application: views, templates, tests
  views.py            upload page, RF calculation, image fetch/serve
  templates/          annotate.html (the single-page annotation UI)
  management/         cleanup_media command
  tests.py            RF calculation + endpoint security tests
```

## License

MIT

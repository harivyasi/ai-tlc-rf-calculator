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
- **Assisted spot detection** — one click finds candidate spots on the plate
  interior using scikit-image Laplacian-of-Gaussian blobs plus lane-profile
  peak detection, shape-gated to reject reference lines and labels; results are
  editable suggestions you review before calculating.
- Two image sources: **local upload** or **fetch from an ELN URL**.
- Server-side RF calculation and image annotation (OpenCV).
- CSV export (download locally or POST back to an ELN).

## Tech stack

- **Django 5.2 LTS** (Python 3.14+); minimal app set (no admin/auth — cookie-based CSRF)
- **OpenCV** (headless) + **NumPy** for image annotation; **scikit-image** +
  **SciPy** for spot detection
- **Gunicorn** + **WhiteNoise** for serving
- **uv** for dependency management
- Self-contained frontend: vanilla JS + CSS in `static/`, **no CDN/JS
  frameworks** (inline SVG icons, system fonts)

## Getting started

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install dependencies into a virtual environment
uv sync

# Run the development server (DEBUG generates a throwaway SECRET_KEY).
# There are no database models, so no migration step is needed.
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
`annotate.html` is kept under `e2e/legacy/templates/` and rendered via
`e2e.legacy.settings`, so the same flow runs against both the old and new
frontends and asserts they produce identical RF results. Further tests document
the fixed image-fetch bugs and cover auto-detect, drag, and delete.

### Linting, formatting, and type checking

```bash
uv run ruff check .          # lint
uv run ruff format .         # format (use --check in CI)
uv run mypy                  # type-check config/ and tlc/
```

Optionally install the pre-commit hooks so these run on every commit:

```bash
uv run pre-commit install
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
| `CSP_CONNECT_SRC`      | *(empty)*                   | Extra origins added to the CSP `connect-src` (e.g. your ELN origin, so "save to ELN" works). |
| `MAX_UPLOAD_MB`        | `16`                        | Max request-body size (MB). Must exceed the base64-inflated 10 MB image cap. |
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
  optional `ELN_ALLOWED_HOSTS` allowlist can restrict it further. Redirects are
  followed (ELN attachment URLs often redirect to storage) but every hop is
  re-validated against the same policy. Downloads are bounded by a timeout and a
  25 MB size cap.
- **Image serving (`/serve-image/`)** is confined to `MEDIA_ROOT`; path
  traversal is not possible.
- CSRF protection is enforced on the calculation endpoint.
- **Content-Security-Policy**: the frontend is fully self-hosted with no inline
  scripts, styles, or event handlers, so the CSP is `default-src 'self'` with
  `script-src`/`style-src` locked to `'self'` (no `unsafe-inline`). Cross-origin
  browser requests are blocked unless their origin is listed in
  `CSP_CONNECT_SRC` — set it to your ELN origin to allow "save to ELN".
- **Resource limits**: request bodies are capped (`MAX_UPLOAD_MB`) and images
  larger than 40 MP are rejected so a single request cannot exhaust CPU/memory
  in the OpenCV paths. The calculation/detection endpoints are CPU-bound and
  unauthenticated; if the tool is internet-facing, add **request rate limiting
  at the reverse proxy** (e.g. nginx `limit_req`) — app-level limiting isn't
  built in because it has no shared state across workers.
- **EXIF orientation** is applied to uploaded images so annotations stay aligned
  with what the browser shows (phone photos often carry rotation metadata).

## Deployment (Docker)

```bash
docker build -t tlc-rf-calculator .
docker run --rm -p 8050:8050 \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  -e ALLOWED_HOSTS="tlc.example.org" \
  -e CSRF_TRUSTED_ORIGINS="https://tlc.example.org" \
  -e CSP_CONNECT_SRC="https://eln.example.org" \
  tlc-rf-calculator
```

### ELN (Chemotion) integration

Chemotion opens the tool with a single signed URL that both serves the plate
image (GET) and receives the annotated results (POST):

```text
https://tlc.example.org/?url=https%3A%2F%2Feln.example.org%2Fapi%2Fv1%2Fpublic%2Fthird_party_apps%2F<jwt>
```

For that flow, set `CSP_CONNECT_SRC` to the ELN origin (so the browser may POST
results back), e.g. `CSP_CONNECT_SRC="https://eln.example.org"`, and optionally
`ELN_ALLOWED_HOSTS="eln.example.org"` to restrict which hosts the server will
fetch images from.

See [docs/eln-integration.md](docs/eln-integration.md) for the full contract
(the signed URL, its JWT fields, the shared GET/POST endpoint, redirect
handling, and CORS/CSP requirements).

The app listens on port `8050` and exposes a health probe at `/healthz`. Static
files are served by WhiteNoise; user-generated media is served by the app (fine
for low-traffic internal use — front it with a reverse proxy for higher load).

Gunicorn is configured via [gunicorn.conf.py](gunicorn.conf.py) and tuned with
environment variables: `GUNICORN_WORKERS` (default 3), `GUNICORN_THREADS`
(2), `GUNICORN_TIMEOUT` (60s), `GUNICORN_BIND` (`0.0.0.0:8050`).

## Continuous integration

- **GitHub Actions** (`.github/workflows/ci.yml`): lint, format check, type
  check, unit tests, and the Playwright e2e suite on every push to `main` and
  pull request; builds and pushes the Docker image to the GitHub Container
  Registry (`ghcr.io`) only when a **release** is published.
- **GitLab CI** (`.gitlab-ci.yml`): the same lint/format/type/unit checks and
  e2e on branches / merge requests; builds and pushes the image to the GitLab
  registry only on a **tag** (release).

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
config/               Django project configuration
  settings.py         env-driven settings (see config/env.py)
  urls.py             root URLs (+ media serving)
  wsgi.py / asgi.py   server entry points
tlc/                  the application
  views.py            upload page, RF calc, spot detection, image fetch/serve
  detection.py        assisted spot detection (blob + lane peaks)
  urls.py             app URL routes
  templates/tlc/      annotate.html (the single-page annotation UI)
  static/tlc/         app.css, app.js (self-contained frontend)
  management/         cleanup_media command
  tests.py            RF calc, detection, and endpoint security tests
e2e/                  Playwright regression suite (vs. the original frontend)
  legacy/             pinned original frontend + settings for comparison
```

## License

MIT

# AI TLC RF Calculator

AI TLC RF Calculator is a Django web application for Thin Layer Chromatography (TLC) image analysis. The app loads TLC plate images from an ELN image URL or a local upload, supports browser-based annotation, runs ONNX model inference for spot and line detection, calculates RF values, and produces annotated image and CSV outputs.

![TLC quick guide](circle_detector/images/initial-quick-guide.png)

## Repository Information

```text
Repository name: ai-tlc-rf-calculator
Application folder: aitlcapp
Django project: rf_calculator
Django app: circle_detector
Primary page title: TLC Image Annotation Tool
```

## What This App Does

- Loads TLC images from an ELN-style URL passed through the page query string.
- Supports local TLC image upload with drag-and-drop or file selection.
- Displays the TLC image on a browser canvas for annotation.
- Adds and edits TLC spot markers.
- Adds Min and Max reference lines for RF calculation.
- Adds horizontal compound reference lines.
- Adds vertical mixture, solvent, or lane reference lines.
- Runs automatic Min/Max line detection using `line_best.onnx`.
- Runs automatic TLC spot detection using `spot_best.onnx`.
- Calculates RF values from spot position and Min/Max line geometry.
- Skips invalid RF values outside the `0.0` to `1.0` range.
- Draws annotated spots, RF values, compound labels, solvent labels, and reference lines on the output image.
- Exports RF result data as CSV.
- Provides browser actions to download CSV, save annotated image, and upload outputs back to the configured ELN endpoint.

## Application Workflow

1. Open the TLC annotation page.
2. Select **From ELN** or **Upload Local** as the image source.
3. For ELN mode, the app reads the `url` query parameter and fetches the image through `/fetch-image/`.
4. For local mode, the selected image is uploaded through `/upload-local-image/`.
5. Add Min and Max lines manually or use **Auto Detect Lines**.
6. Add TLC spots manually or use **Auto Detect Spots** after Min/Max lines are available.
7. Add horizontal and vertical annotation labels when needed.
8. Click **Calculate RF**.
9. Review the RF table and annotated TLC image.
10. Export the CSV or annotated image.

## Tech Stack

| Area | Technology |
|---|---|
| Backend framework | Django `>=4.2,<5.0` (LTS) |
| Runtime image | Python `3.11-slim` Docker base image |
| Dependency management | `pyproject.toml` + [uv](https://docs.astral.sh/uv/) with a committed `uv.lock` |
| HTTP server | Gunicorn `>=23.0` (`gthread` worker) |
| Static files | WhiteNoise `>=6.6` |
| HTTP client | Requests `>=2.32.3` |
| Computer vision | OpenCV Headless `>=4.6.0.66` |
| Numerical processing | NumPy `>=1.26.4,<2.0` |
| Image handling | Pillow `>=10.4` |
| ONNX inference | ONNX Runtime `>=1.19.2` |
| Tests | pytest + pytest-django |
| Frontend | HTML, CSS, JavaScript |
| UI libraries | Materialize CSS, SweetAlert2, Material Icons, Inter -- all vendored locally, no CDN |
| Database | SQLite (unused: the app defines no models) |

## Project Structure

```text
aitlcapp/
├── circle_detector/
│   ├── images/
│   │   └── initial-quick-guide.png
│   ├── migrations/
│   │   └── __init__.py
│   ├── ml_models/
│   │   ├── __init__.py
│   │   ├── line_best.onnx
│   │   └── spot_best.onnx
│   ├── static/
│   │   └── images/
│   │       └── initial-quick-guide.png
│   ├── templates/
│   │   └── circle_detector/
│   │       ├── annotate.html
│   │       ├── result.html
│   │       └── upload.html
│   ├── tests/                  # pytest suite
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── line_onnx.py
│   ├── media_views.py          # hardened /media/ serving
│   ├── middleware.py           # Content-Security-Policy
│   ├── models.py
│   ├── paths.py                # path containment + filename sanitising
│   ├── remote.py               # SSRF-guarded ELN image fetching
│   ├── spot_onnx.py
│   ├── urls.py
│   └── views.py
├── rf_calculator/
│   ├── __init__.py
│   ├── asgi.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── static/
├── .dockerignore
├── .gitattributes
├── .python-version
├── Dockerfile
├── manage.py
├── pyproject.toml
└── uv.lock
```

## ONNX Model Files

The app loads both ONNX models during Django startup:

```text
circle_detector/ml_models/spot_best.onnx
circle_detector/ml_models/line_best.onnx
```

Current model configuration from `rf_calculator/settings.py`:

```python
SPOT_MODEL_PATH = BASE_DIR / "circle_detector" / "ml_models" / "spot_best.onnx"
SPOT_IMGSZ_DEFAULT = 1280
SPOT_CONF_DEFAULT = 0.15
TLC_BAND_MARGIN = 12

LINE_MODEL_PATH = BASE_DIR / "circle_detector" / "ml_models" / "line_best.onnx"
LINE_IMGSZ_DEFAULT = 1024
LINE_CONF_DEFAULT = 0.05
```

Model file sizes in the uploaded app package:

```text
line_best.onnx: about 12 MB
spot_best.onnx: about 44 MB
```

## RF Calculation Logic

The backend calculates RF values in `circle_detector/views.py` using the spot position and selected reference lines:

```python
rf_value = (spot_y - min_line_y) / (max_line_y - min_line_y)
rf_value = round(rf_value, 2)
```

Only RF values strictly between `0.0` and `1.0` are included in the CSV and response.

The CSV columns generated by the app are:

```text
Solvent (X value), Compound (Y value), RF value
```

The frontend download CSV action uses these column names:

```text
mixture_label (X axis), substance_label (Y axis), RF value
```

## API Endpoints

| Method | Endpoint | Function |
|---|---|---|
| GET | `/` | Opens the TLC annotation interface. |
| POST | `/upload-local-image/` | Uploads a local TLC image to `media/local_uploads/`. |
| GET | `/fetch-image/?url=...` | Validates and downloads a remote image, storing it as `media/downloaded_<random>.<ext>` and returning that **media-relative** name. |
| GET | `/serve-image/?file_path=...` | Serves an image by **media-relative** path. Absolute paths and anything outside `MEDIA_ROOT` return 404. |
| GET | `/media/<path>` | Serves annotated images and CSVs. Always available, extension-restricted. |
| POST | `/auto_detect_lines/` | Runs `line_best.onnx` and returns Min/Max line positions. |
| POST | `/auto_detect_spots/` | Runs `spot_best.onnx` inside the Min/Max band and returns detected spot centers. |
| POST | `/calculate_rf/` | Calculates RF values and creates annotated image plus CSV files. |
| GET | `/admin/` | Django admin route. |

## Endpoint Payloads

### Auto Detect Lines

```json
{
  "isLocalImage": true,
  "imageSrc": "local_upload.png",
  "imageData": "data:image/png;base64,...",
  "localImagePath": "local_uploads/image.png"
}
```

Example response:

```json
{
  "minY": 120.5,
  "maxY": 740.2,
  "debug": {
    "image_size": [1024, 768],
    "conf": 0.05,
    "raw_detections": 2
  }
}
```

### Auto Detect Spots

```json
{
  "isLocalImage": true,
  "imageSrc": "local_upload.png",
  "imageData": "data:image/png;base64,...",
  "localImagePath": "local_uploads/image.png",
  "minY": 120.5,
  "maxY": 740.2
}
```

Example response:

```json
{
  "spots": [
    {
      "x": 300.4,
      "y": 420.8,
      "conf": 0.86
    }
  ],
  "debug": {
    "image_size": [1024, 768],
    "roi": [0, 108, 1024, 752],
    "minY": 120.5,
    "maxY": 740.2,
    "kept": 1
  }
}
```

### Calculate RF

```json
{
  "lines": [
    {"type": "min", "y": 700},
    {"type": "max", "y": 100},
    {"type": "vertical", "x": 300, "text": "Mixture A"},
    {"type": "horizontal", "y": 420, "text": "Compound A"}
  ],
  "spots": [
    {"x": 300, "y": 420}
  ],
  "imageSrc": "downloaded_a1b2c3d4.png",
  "isLocalImage": false,
  "canvasWidth": 1024,
  "canvasHeight": 768,
  "scaleFactor": 1
}
```

Example response:

```json
{
  "rf_values": [
    {
      "compound": "Compound A",
      "solvent": "Mixture A",
      "rf": 0.47
    }
  ],
  "annotated_image_url": "/media/annotated_downloaded_image_1772283241.png",
  "csv_url": "/media/rf_values_downloaded_image_1772283241.csv",
  "message": "Successfully calculated RF values for 1 spots"
}
```

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/shilpa2806/ai-tlc-rf-calculator.git
cd ai-tlc-rf-calculator
```

### 2. Install uv

See the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/), or:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Install dependencies

`uv` creates the virtual environment and installs the exact locked versions:

```bash
uv sync
```

The Python version comes from `.python-version` (3.11); uv downloads it if needed.

### 4. Set local environment variables

Only `DEBUG` is needed locally. With `DEBUG=True` a throwaway `SECRET_KEY` is
generated per run, so nothing else has to be configured:

```bash
export DEBUG=True
```

With `DEBUG=False` an explicit `SECRET_KEY` is **required** and the app refuses
to start without one. `ALLOWED_HOSTS` defaults to `*`, so it does not need to be
set for local use.

### 5. Apply migrations

```bash
uv run python manage.py migrate
```

### 6. Run the development server

```bash
uv run python manage.py runserver
```

Local URL:

```text
http://127.0.0.1:8000/
```

### 7. Run the tests

```bash
uv run pytest                  # everything
uv run pytest -m 'not slow'    # skip the tests that load the ONNX models
```

## Docker Setup

Build the image:

```bash
docker build -t ai-tlc-rf-calculator .
```

Run the container:

```bash
docker run --rm -p 8050:8050 \
  -e SECRET_KEY="$(openssl rand -base64 48)" \
  -v tlc-media:/code/media \
  ai-tlc-rf-calculator
```

Docker URL:

```text
http://localhost:8050/
```

Notes on the two flags:

- `SECRET_KEY` is mandatory. The container exits immediately without one rather
  than falling back to a built-in value.
- `-v tlc-media:/code/media` is strongly recommended. Annotated images and CSVs
  are written there and the UI links to them, so without a volume they are lost
  on restart. Override the location with `MEDIA_ROOT` if you prefer.

`ALLOWED_HOSTS` defaults to `*`, so the image runs on any host without
configuration; validating the `Host` header is left to whatever proxy sits in
front. Set `ALLOWED_HOSTS` explicitly to narrow it.

Behind a TLS-terminating proxy, add:

```bash
  -e TRUST_PROXY_SSL_HEADER=True \
  -e SECURE_SSL_REDIRECT=True \
  -e SESSION_COOKIE_SECURE=True \
  -e CSRF_COOKIE_SECURE=True \
  -e SECURE_HSTS_SECONDS=31536000 \
  -e CSRF_TRUSTED_ORIGINS=https://tlc.example.org
```

With those set, `manage.py check --deploy` reports no issues. `TRUST_PROXY_SSL_HEADER`
must only be enabled when a proxy really is in front: otherwise any client can
assert `X-Forwarded-Proto: https` and defeat the checks above.

The image serves requests with the Gunicorn `gthread` worker (2 processes x 4
threads). The two ONNX models are loaded lazily on the first detection request
and add roughly 250 MB per process, so budget about 900 MB once both are warm.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | *(required when `DEBUG=False`)* | Django signing key. No fallback: the app refuses to start without it. |
| `DEBUG` | `False` | Debug mode. Off by default so a forgotten variable cannot ship a debug build. |
| `ALLOWED_HOSTS` | `*` | Comma-separated host allowlist. |
| `MEDIA_ROOT` | `<project>/media` | Where annotated images and CSVs are written. Mount a volume here. |
| `ELN_ALLOWED_HOSTS` | *(empty)* | Comma-separated hosts `/fetch-image/` may fetch from, and that the page may upload results to. Empty means any public host, with internal ranges refused. **Set this in production.** |
| `ELN_FETCH_MAX_BYTES` | `26214400` (25 MB) | Size cap on a fetched image. |
| `ELN_CONNECT_TIMEOUT` / `ELN_READ_TIMEOUT` | `5` / `20` | Seconds. |
| `MEDIA_RETENTION_HOURS` | `24` | How long fetched ELN images are kept before being swept. |
| `ENABLE_ADMIN` | `False` | Exposes `/admin/`. Off by default: there are no models registered, so it is only a login surface. |
| `TRUST_PROXY_SSL_HEADER` | `False` | Trust `X-Forwarded-Proto`. Enable **only** behind a real proxy. |
| `SECURE_SSL_REDIRECT` | `False` | Redirect HTTP to HTTPS. |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | `False` | Mark cookies HTTPS-only. |
| `SECURE_HSTS_SECONDS` | `0` | Enable HSTS. |
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | Comma-separated, **scheme required** (`https://host`). Note `ALLOWED_HOSTS=*` does not relax the CSRF origin check. |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | `41943040` (40 MB) | Request body cap; plate images arrive base64-encoded in JSON. |
| `FILE_UPLOAD_MAX_MEMORY_SIZE` | `10485760` (10 MB) | Multipart upload cap. |
| `LOG_LEVEL` | `INFO` | Root log level. |

## GitHub Push Commands

```bash
git init
git branch -M main
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/shilpa2806/ai-tlc-rf-calculator.git
git push -u origin main
```

If the remote already exists:

```bash
git remote set-url origin https://github.com/shilpa2806/ai-tlc-rf-calculator.git
git push -u origin main
```

## Files to Keep Out of Git

The uploaded app package contains runtime and generated files. These should stay out of GitHub:

```text
.git/
db.sqlite3
media/
staticfiles/
__pycache__/
*.pyc
.env
venv/
```

Recommended `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
*.pyo
*.pyd

# Virtual environments
venv/
.venv/
env/

# Django
db.sqlite3
staticfiles/
media/

# Environment
.env
.env.*

# OS and editor files
.DS_Store
Thumbs.db
.vscode/
.idea/
```

## Security Notes

The following were fixed and are covered by tests in `circle_detector/tests/`:

- **`/serve-image/` no longer accepts absolute paths.** `file_path` is
  media-relative and resolved against `MEDIA_ROOT` with a containment check, so
  it can no longer be used to read arbitrary files. Symlinks out of the media
  directory are refused too.
- **`/fetch-image/` validates its target.** Only `http`/`https`; internal
  address ranges (loopback, private, link-local including `169.254.169.254`,
  CGNAT, reserved, multicast) are refused after DNS resolution; redirects are
  followed but **every hop is revalidated**; the body is capped as it streams;
  and the stored file must decode as a real image.
- **Output filenames cannot steer writes.** `imageSrc` is reduced to a bare
  stem, so it cannot contain a directory component, and a random token replaces
  the previous second-granular timestamp that collided under concurrency.
- **Uploads derive their extension from the file contents**, not the filename or
  the client's `Content-Type`. Previously `evil.html` sent as `image/png` was
  stored as `.html`.
- **Media is served in production.** Annotated images and CSVs used to 404
  whenever `DEBUG=False`, which meant the app's main output was unreachable.
  The media route is now always registered, restricted to an extension
  allowlist, and sends `nosniff` plus a sandboxed CSP.
- **No CDN dependency.** Materialize, SweetAlert2, Material Icons and Inter are
  vendored under `circle_detector/static/circle_detector/vendor/`, so the app
  works on an isolated network. See that directory's `LICENSES.md`.
- **Safe defaults.** `DEBUG` defaults to off and `SECRET_KEY` has no fallback.

### Known limitations

- **There is no authentication.** Anyone who can reach the service can run
  inference and write files into the media directory. Put it behind SSO or a
  private network before exposing it.
- **`@csrf_exempt` remains on the four POST endpoints.** The frontend already
  sends `X-CSRFToken`, so removing it is likely straightforward, but it is a
  behaviour change for any external API consumer and is deliberately left for a
  separate change.
- **DNS rebinding is not fully closed.** A window exists between address
  validation and the connection in `/fetch-image/`. Setting `ELN_ALLOWED_HOSTS`
  makes it irrelevant, because allowlisting is by name.
- **The CSP still needs `'unsafe-inline'` for scripts**, because the annotation
  page carries its behaviour inline. Extracting it into a static file is what
  would allow that to be dropped.
- **Media growth is only partly bounded.** Fetched ELN images are swept after
  `MEDIA_RETENTION_HOURS`, but annotated images and CSVs accumulate and need a
  retention policy of their own.
- **SQLite is configured but unused** -- the app defines no models. A real
  database is only needed if persistence is added.

Run `python manage.py check --deploy` before go-live; with the TLS variables set
it reports no issues.

## Troubleshooting

### ONNX model loading error

Check that both model files exist:

```text
circle_detector/ml_models/spot_best.onnx
circle_detector/ml_models/line_best.onnx
```

### No spots detected

- Draw Min and Max lines first.
- Verify the TLC spots are inside the Min/Max band.
- Review `SPOT_CONF_DEFAULT` in `rf_calculator/settings.py`.

### No lines detected

- Use a clearer TLC image.
- Review `LINE_CONF_DEFAULT` in `rf_calculator/settings.py`.

### No RF values returned

- Confirm that at least one spot is between the Min and Max lines.
- RF values outside `0.0` and `1.0` are skipped.
- Confirm that canvas width and height are passed in the `/calculate_rf/` payload.

## License


## Maintainer

Shilpa Thotli

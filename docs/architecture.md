# Architecture & design

Developer-oriented reference for the TLC RF Calculator: how it's put together
and *why* the main decisions were made. For end-user instructions see the
[README](../README.md); for the Chemotion contract see
[eln-integration.md](eln-integration.md).

## 1. What it is

A single-page browser tool for computing **retention factor (RF)** values from a
TLC plate image. The user annotates reference lines and spots on a canvas; the
server computes RF, burns the results into a full-resolution annotated image, and
exports CSV. It runs standalone or embedded in the Chemotion ELN workflow.

```text
RF = (spot_y − min_line_y) / (max_line_y − min_line_y)
```

- **Backend:** Django 5.2 LTS on Python 3.14. No database. Stateless request
  handlers + a `media/` scratch dir for generated files.
- **Frontend:** vanilla JS as native ES modules + CSS, no framework, no CDN,
  no bundler. Served as static files.
- **Compute:** OpenCV (headless) + NumPy for drawing/RF; scikit-image + SciPy for
  assisted spot detection.

## 2. High-level flow

```text
 browser (annotate.html + ES modules)                 Django (config/ + tlc/)
 ────────────────────────────────────                 ───────────────────────
 load image ──(local: base64)──────────────────────►  (decoded server-side)
             └─(ELN: ?url=)──► GET /fetch-image/ ────► SSRF-guarded fetch → MEDIA
                               GET /serve-image/ ◄────  confined to MEDIA_ROOT
 annotate on <canvas> (lines, spots, plate area)
 Auto-detect ─────────────────► POST /detect-spots/ ─► detection.detect_spots()
 Calculate RF ────────────────► POST /calculate_rf/ ─► RF + burn labels → PNG/CSV
 export / save-back ──(POST multipart, cross-origin)─► ELN endpoint (?url=)
```

Endpoints (`tlc/urls.py` → `tlc/views.py`): `upload_image` (the page),
`calculate_rf`, `detect_spots`, `fetch_and_save_image` (`/fetch-image/`),
`serve_image` (`/serve-image/`), `healthz`.

## 3. Key design choices (and why)

### No database, minimal app set
There are no models. `DATABASES = {}`, and only `django.contrib.staticfiles` +
`tlc` are installed — no admin/auth/sessions/messages. CSRF protection is
cookie-based and needs no session store. Rationale: the tool is a pure
image→numbers transform; persistence is the ELN's job. Dropping the DB and those
apps removes migrations, a whole class of auth bugs, and most of the attack
surface. Generated files live in `MEDIA_ROOT` under random names and are pruned
by the `cleanup_media` management command.

### Server does the compute, browser does the interaction
Annotation is inherently interactive (drag, click, zoom), so it lives in the
browser on a `<canvas>`. The numerically heavy and dependency-heavy work — LoG
blob detection, connected-component shape gating, high-quality label rendering on
multi-megapixel images — runs server-side where NumPy/SciPy/OpenCV are available
and fast. (See §9 for how this could move fully client-side via WASM.)

### Vanilla ES modules, no framework/CDN
The frontend is plain JS split into ES modules under `tlc/static/tlc/js/`, with
an inline SVG icon sprite and system fonts. No React/Vue, no bundler, nothing
loaded from a CDN. Rationale: the UI is small and mostly canvas work; a framework
would add weight and a build step for little gain. Crucially, being 100%
self-hosted lets the **CSP** be strict (`default-src 'self'`, no `unsafe-inline`)
— every script, style, and handler is same-origin. Handlers are wired by a
`data-action` dispatch table (no inline `onclick`).

### Coordinate model
This is the subtle core of the frontend. Three spaces:
- **Original-image px** — the source bitmap's pixels. All persisted annotation
  data (`line.y`/`line.x`, `spot.x`/`spot.y`, `plateRegion`) and everything sent
  to the server is in this space.
- **Canvas bitmap px** — the `<canvas>` backing store, fit to ≤500 px tall.
  `window.scaleFactor = originalH / canvasH` converts between the two.
- **Canvas display (CSS) px** — the element's rendered size, which `max-width`
  can shrink below the bitmap size.

`pointerCanvas(e)` (canvas.js) maps a pointer event to **bitmap** px using
`getBoundingClientRect()`, so clicks stay aligned even when the canvas is
CSS-scaled — this was the fix for the "annotations land off the cursor" bug. HTML
delete-icon overlays live in `#canvas-container` and must be placed in **display**
px, so their positions are scaled by `clientWidth/width` (the fix for the delete
cross drifting off a scaled canvas). Rotation (0/90/180/270) is applied in
`drawImageRotated` on the client and re-applied server-side in `_rotate_image`;
it's only allowed on a clean image so the RF axis never has to be re-derived.

### One funnel for edits: `commitChange()` → undo/redo + stale-marking
Every completed annotation edit routes through `canvas.commitChange()`, which
does three things in one place: record a history snapshot (undo/redo), mark any
prior result stale, and repaint (`applyCrop()` → `redrawCanvas()` → `refreshUI()`).
`addSpot` is a plain mutator; callers commit at the *gesture* boundary so a click
or an Auto-detect batch is a single undo step. Undo/redo restore shares the same
tail (`afterAnnotationChange`). Rationale: undo/redo and "results are now out of
date" are the same event ("annotations changed"); funnelling them prevents the
two from drifting and means a future mutation site only has to call one function.
History (`history.js`) is a deep-copied snapshot stack with a cursor (snapshots
omit transient `deleteIcon` DOM refs).

### Stale-results guard
The results table + annotated image are a snapshot from the last Calculate RF.
Any later edit (add/move/delete spot, relabel, resize labels, redraw the plate
area) shows a banner, pulses **Calculate RF**, dims the annotated image, and
**disables export** until recalculation. Rationale: the annotated PNG is what gets
saved / pushed to the ELN, so exporting a stale image would silently record wrong
data. Chosen over auto-recalculating on every edit (too many server round-trips)
and over silently keeping the old image.

### Plate Area is a detection region, decoupled from Min/Max
The **Plate Area** box only confines Auto-detect. It deliberately does **not**
place the Min/Max lines, because in TLC the baseline sits *above* the plate's
bottom edge and the solvent front *below* the top edge — the plate boundary is
not the RF reference. The user places Min (baseline) and Max (solvent front) from
the actual image. While a plate area is set, all lines/spots are confined to it:
placement outside is blocked, drags clamp to the edges, and redrawing the box
clamps lines to the new bounds and **drops** spots that fall outside (a spot is a
specific location — snapping it to an edge would fabricate data). Clamp/contains
math is in `geometry.js`.

### Assisted spot detection (`tlc/detection.py`)
`detect_spots(image, *, region, ring_center)` combines two complementary signals
on a background-subtracted, plate-masked darkness map:
1. **scikit-image Laplacian-of-Gaussian blobs** — round dark spots.
2. **Lane-profile peak finding** (`scipy.signal.find_peaks` per column +
   `peak_local_max`) — catches faint spots the blob detector misses.
Candidates are **shape-gated** via `cv2.connectedComponentsWithStats`
(compactness, aspect ratio, area) to reject reference lines and label strokes.
Work happens at a **bounded resolution** (`PROCESS_MAX_DIM = 1200`, radii scaled
back to full res) so a 12 MP photo processes in ~2.5 s instead of ~2 min. An
optional `region` (the plate area) crops detection; `ring_center` centres on a
blob's bounding box instead of its centroid (better for hand-drawn rings).
Detection is best-effort — results are editable suggestions the user reviews.

### Annotation rendering: preview vs. export
The live canvas draws colour-coded reference lines (Min green, Max red, H purple,
V blue) and orange spot rings for interaction. The **saved** annotated image is
rendered server-side (`_draw_annotations`) by burning only the *results* — spot
rings + RF/compound/solvent labels — into the **full-resolution** image;
reference/label lines are intentionally not saved. The crop view and the 500 px
canvas cap are display-only; the export keeps the original dimensions (and any
rotation). Text size is a **percentage multiplier** on the size auto-derived from
the image resolution — a fixed pt/px value would mean different things on a 3 MP
vs a 12 MP plate.

### Security model
- **SSRF** (`_is_safe_remote_url` + `_fetch_following_safe_redirects`): only
  http(s), host must resolve to a public address, optional allowlist; redirects
  followed but **every hop re-validated** (ELN URLs redirect to storage); timeout
  + 25 MB cap.
- **Path traversal** (`_resolve_within_media`): `basename` + `realpath` +
  `commonpath`, so `/serve-image/` can't escape `MEDIA_ROOT`.
- **CSRF** enforced on `calculate_rf`; **strict CSP** (see above); secure
  cookies / HSTS / nosniff / `DENY` framing when `DEBUG` is off; non-root Docker.
- **Resource limits**: `MAX_UPLOAD_MB` body cap and a 40 MP image guard so one
  request can't exhaust CPU/memory. Endpoints are unauthenticated and CPU-bound —
  put rate limiting at the reverse proxy for internet-facing deploys.
- **EXIF orientation** applied on decode so annotations align with the browser.

### Config derivation (fewer env vars)
A real deployment sets three vars: `SECRET_KEY`, `ALLOWED_HOSTS`, `ELN_ORIGIN`.
`CSRF_TRUSTED_ORIGINS` derives `https://<host>` from `ALLOWED_HOSTS`
(`env.csrf_origins_from_hosts`); `ELN_ORIGIN` sets **both** the CSP `connect-src`
and the fetch allowlist (`ELN_ALLOWED_HOSTS`). Each derived value is still
overridable. `ELN_ALLOWED_HOSTS` is a Django setting (single source of truth);
`views.py` reads `settings.ELN_ALLOWED_HOSTS`.

## 4. Frontend module map (`tlc/static/tlc/js/`)

Loaded as `<script type="module" src="js/main.js">`. Leaf → entry:

| Module | Responsibility |
| --- | --- |
| `state.js` | The single mutable `state` object + `pageUrl`/`elnUrl`. Imports nothing. |
| `geometry.js` | Pure `clamp` / `pointInRegion`. Leaf. |
| `history.js` | Undo/redo snapshot stack + cursor. |
| `ui-kit.js` | Toasts, busy overlay, cookie/timestamp helpers. |
| `theme.js` | Light/dark toggle (persisted). |
| `ui.js` | Headings, source card, hint, layout, Calculate/Rotate/undo-redo enable state, **stale-results** controller, `textScale()`. |
| `canvas.js` | Draw/redraw, `pointerCanvas`, crop view, rotate, reset, `commitChange`, undo/redo drivers. |
| `annotations.js` | Line/spot tools, popup, delete, active-button, plate-area confinement on placement. |
| `interaction.js` | Pointer drag / region box / double-click(-tap) delete; drag clamping. |
| `results.js` | RF calc + detect requests, results table, CSV, ELN save. |
| `sources.js` | ELN/local source toggle, upload, fetch. |
| `main.js` | `data-action` dispatch table, keyboard shortcuts, init. |

Notes: `state.canvas`/`ctx` are assigned in `init()` — never captured at import.
The unavoidable `canvas ↔ annotations` import cycle is safe because every
cross-module export is a **hoisted function declaration** (no TDZ). All shared
mutable state lives on the `state` object (imported bindings are read-only).

## 5. Backend module map

| File | Responsibility |
| --- | --- |
| `config/settings.py` | Env-driven settings; CSP middleware config; config derivation. |
| `config/env.py` | `env_bool/int/list`, `csrf_origins_from_hosts`, `host_of`. |
| `config/middleware.py` | `ContentSecurityPolicyMiddleware`. |
| `config/urls.py` | Root URLs + media serving. |
| `tlc/views.py` | Page, RF calc, detection, ELN fetch/serve, and the security helpers. |
| `tlc/detection.py` | Assisted spot detection. |
| `tlc/management/commands/cleanup_media.py` | Prune old generated files. |

## 6. Testing strategy

- **Unit** (`tlc/tests.py`, ~66): RF math, detection (count/blank/region/
  ring-center), the security helpers (SSRF allowlist via `@override_settings`,
  path confinement, decode/oversize branches), `_draw_annotations` text scaling,
  config derivation (a subprocess boots settings with `ELN_ORIGIN`), env helpers.
- **E2E** (`e2e/test_regression.py`, Playwright + Chromium): drives the real UI —
  RF flow, drag, delete, Auto-detect, plate-area confinement, rotate, theme,
  crop, touch, text size, stale-results, undo/redo. It doubles as a **regression
  harness**: the pre-refactor frontend is pinned under `e2e/legacy/` and the same
  flow is asserted to produce identical RF on both. `conftest.py` fails a test on
  any uncaught page error or module-load error.
- **JS units without npm** (`e2e/test_js_units.py`): the page dynamically
  `import()`s modules and asserts on pure exports (`geometry`, `isHorizontalLine`,
  `textScale`) — real unit coverage with no Node toolchain.

Run: `DEBUG=True uv run python manage.py test` and
`uv run pytest e2e`. Lint/type: `uv run ruff check .`, `uv run ruff format
--check .`, `uv run mypy`.

## 7. Build, CI, deployment

- **Dev:** `uv sync`; `DEBUG=True uv run python manage.py runserver` (throwaway
  key, no migrations).
- **CI:** GitHub Actions + GitLab CI run lint/format/type/unit/e2e on
  branches/PRs; the Docker image is built + pushed only on a release/tag.
- **Container:** multi-stage `Dockerfile` (uv sync + collectstatic → slim
  runtime), non-root user, TCP healthcheck, `gunicorn -c gunicorn.conf.py`
  (env-tuned: `GUNICORN_WORKERS`/`THREADS`/`TIMEOUT`/`BIND`). Listens on `8050`,
  health probe at `/healthz`. WhiteNoise serves static; the app serves media
  (front with a reverse proxy for load).
- **Static caveat:** `main.js` is hashed by `ManifestStaticFilesStorage`, but its
  relative `import "./x.js"` statements are resolved by the browser and are **not**
  hash-rewritten — sibling modules are served unhashed (no cache-busting). Bump a
  query string or clear caches on a breaking sibling change.

Verify a deployment with `uv run python manage.py check --deploy`.

```bash
docker build -t tlc-rf-calculator .
docker run --rm -p 8050:8050 \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  -e ALLOWED_HOSTS="tlc.example.org" \
  -e ELN_ORIGIN="https://eln.example.org" \
  tlc-rf-calculator
```

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `SECRET_KEY` | *(required if not DEBUG)* | Django secret key; a throwaway key is generated only under `DEBUG=True`. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hosts. |
| `ELN_ORIGIN` | *(empty)* | ELN origin (e.g. `https://eln.example.org`); sets CSP `connect-src` + fetch allowlist. |
| `DEBUG` | `False` | Debug mode. Never enable in production. |
| `MAX_UPLOAD_MB` | `16` | Max request-body size (MB); must exceed the base64-inflated 10 MB image cap. |
| `SECURE_SSL_REDIRECT` | `True` (prod) | Redirect HTTP→HTTPS. |
| `SECURE_HSTS_SECONDS` | `31536000` | HSTS max-age (prod). |
| `GUNICORN_WORKERS`/`THREADS`/`TIMEOUT`/`BIND` | `3`/`2`/`60`/`0.0.0.0:8050` | Gunicorn tuning. |

Derived, override only if needed: `CSRF_TRUSTED_ORIGINS` (from `ALLOWED_HOSTS`),
`CSP_CONNECT_SRC` and `ELN_ALLOWED_HOSTS` (from `ELN_ORIGIN`).

### Maintenance

Generated files accumulate in `MEDIA_ROOT`; prune from cron:

```bash
uv run python manage.py cleanup_media --hours 24 [--dry-run]
```

## 8. ELN (Chemotion) integration

Chemotion opens the tool with one signed URL (`?url=<jwt endpoint>`) used for
**both** the image GET and the results POST. The server proxies the GET
(`/fetch-image/`) to avoid browser CORS and to SSRF-guard it; the results POST is
sent cross-origin straight from the browser, so it depends on the ELN's CORS and
the tool's `CSP_CONNECT_SRC`/`ELN_ORIGIN`. Full contract, JWT fields, and
worked config: [eln-integration.md](eln-integration.md).

## 9. Possible future: fully client-side (WASM)

The app could become a static, serverless site: RF calc + annotated-image export
are plain `<canvas>` work, and spot detection could run in-browser via
**Pyodide** (NumPy/SciPy/scikit-image ship as WASM). The OpenCV calls would need
PIL/canvas/`skimage` substitutes (`cv2` isn't in Pyodide), and the ELN image GET
would need the ELN to enable CORS (the `/fetch-image/` proxy exists precisely to
avoid that today). Not implemented — recorded as a direction.

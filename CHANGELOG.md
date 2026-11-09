# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [3.0.0] — Unreleased

Joins Shilpa Bai Thotli's ONNX-based TLC Analyser (the tool described in the
paper's Supplementary Information) with the version-2 stack and UI. Results
follow the SI; candidate detection is Shilpa's.

### Changed (breaking)

- Candidate detection uses the trained models **TLC-SpotDet**
  (`spot_best.onnx`) and **TLC-LineDet** (`line_best.onnx`) instead of the
  LoG / lane-peak detector. scikit-image and SciPy are no longer dependencies.
- `POST /detect-spots/` requires `minY` and `maxY`: spots are searched only
  between the baseline and the solvent front (±12 px), as in Shilpa's tool. A
  Plate Area additionally limits the x-range. The response adds `conf` per
  spot, `roi`, and `model`. Candidates need a confidence of at least 0.25,
  the cutoff built into `spot_best.onnx` (it returns no lower scores; the
  0.15 of Shilpa's tool therefore found no more).
- The "ring marks" detection option is removed (it applied only to the LoG
  detector).
- Rf retention follows SI Eq. 9: spots with **0 ≤ Rf ≤ 1** are kept, tested
  before rounding. Previously 0 and 1 were excluded after rounding, so a spot
  on the baseline was dropped.
- `calculate_rf` rejects a Min line above the Max line (SI Eq. 4) and
  non-numeric positions.
- Image limits: 25 MB per file (`MAX_IMAGE_MB`), 64 MP (`MAX_IMAGE_MEGAPIXELS`),
  so phone photos of plates (13 MB / 50 MP) load.
- The CSV is written by the server and its columns follow the table:
  `spot, mixture_label, mixture_type, substance_label, substance_type,
  RF value` (previously `mixture_label, substance_label, RF value`). The
  `CHMO:0001007` line and the `Details:` line are kept. Labels are quoted,
  so a comma no longer shifts the columns. Chemotion stores the file as an
  attachment without parsing it.
- The annotated image shows the baseline, solvent front and markers again
  (SI 8.2), and numbers every spot; rows are sorted by lane label, then Rf.

### Added

- `POST /detect-lines/` and a **Detect Lines** button: proposes the baseline
  and solvent front, assigned by position, with an estimate (flagged in the
  response and the UI) when only one line is found.
- Detection responses identify the model (name, file, SHA-256, export date).
- `ONNX_INTRA_OP_THREADS` caps ONNX Runtime's per-inference threads; the
  server is sized for the models (2 workers x 4 threads).
- With a Plate Area set, **Detect Lines** searches only inside it.
- `tools/legacy_equivalence.py`: plate-by-plate comparison with Shilpa's
  tool (223 plates, 4,941 spot candidates, identical).
- Label types for H/V markers (dropdown plus free text), editable after
  placing; "full labels" option for the saved image; compact numbered table
  with lane/compound type columns. Min/Max buttons hide once placed. H-lines
  are black.
- `POST /upload-image/`: a local image is stored once and later requests
  refer to it by name, instead of each carrying the whole image (~17 MB for a
  13 MB phone photo). Requests fall back to sending it inline if the upload
  fails.
- Files in `MEDIA_ROOT` are deleted automatically once unused for
  `MEDIA_MAX_AGE_HOURS` (default 24), checked at most every
  `MEDIA_SWEEP_MINUTES` (default 15); no cron job needed. An image counts as
  used whenever a request reads it. `cleanup_media` shares the code and
  defaults to the same age.
- Image jobs start only when the memory they need is free (estimated from
  the pixel count and whether the image is already decoded: ~760 MB for a
  50 MP photo decoded afresh; read from the container's cgroup, else
  `/proc/meminfo`; claims shared between workers). Otherwise they wait up to
  30 s, then get 503 "busy, try again". In a container with 2 workers x 4
  threads, 1.5 GB served eight 50 MP requests at once (peak 1.26 GB).
  Settings: `IMAGE_JOB_RESERVE_MB`, `IMAGE_JOB_WAIT_SECONDS`,
  `IMAGE_JOB_CLAIMS_FILE`.
- Each worker keeps recently decoded images (`IMAGE_CACHE_MB`, default 256),
  so Detect Lines, Detect Spots and Calculate RF decode a plate once: on a
  50 MP photo the three steps take 7.3 s instead of 10.4 s, and peak memory
  halves.

### Changed

- ONNX Runtime's CPU memory arena is off (`ONNX_CPU_MEM_ARENA`): it held
  ~500 MB per worker after a large plate; results are identical and speed
  the same within noise. Freed memory is returned to the system after each
  image job.
- `SECRET_KEY` is optional: nothing in the app signs data with it, so a
  random key is made at start-up when it is not set. A deployment needs only
  `ALLOWED_HOSTS` and `ELN_ORIGIN`. Should logins, sessions or messages be
  added later, production refuses to start until a fixed key is set.

### Fixed

- The TLC-LineDet class ids were hard-coded the wrong way round in Shilpa's
  view; they are now read from the model (no effect on results, which are
  assigned by position).
- Phone photos with an EXIF orientation are analysed the way the browser
  shows them: one decoder for uploads and ELN images applies the orientation.
  Shilpa's tool analysed such photos sideways.
- With the H-Line or V-Line tool selected, clicking where the new line
  crosses an existing one no longer drags the existing line and swallows the
  next click.
- Uploads to the ELN no longer send this app's CSRF token to Chemotion, and
  name the file (`TLC_Rf_2026-11-09_14-32.csv`) instead of a bare timestamp.
  A refused upload is reported as a failure; it used to say "Saved".
- Image size limits are checked from the file header, before decoding;
  an oversized image used to be decoded in full first. ELN images are
  validated the same way (they were decoded in full only to check them)
  and stored under their real format.
- `/healthz` answers over plain HTTP in production instead of redirecting to
  HTTPS, so health probes from inside a cluster or host see "ok".

### Removed

- The GitLab CI configuration; the project builds and tests on GitHub
  Actions only.

## [2.0.0] — 2026-07-27

A full hardening, modernization, and feature overhaul of the v1 tool.

### Security

- Fixed path traversal in image serving (confined to `MEDIA_ROOT`).
- Added an SSRF guard to remote image fetching (scheme + public-IP checks,
  optional `ELN_ALLOWED_HOSTS` allowlist, timeout, size cap); redirects are
  followed but each hop is re-validated.
- Re-enabled CSRF protection on the calculation endpoint.
- Strict Content-Security-Policy (`default-src 'self'`, no `unsafe-inline`);
  removed all inline scripts, styles, and event handlers.
- Secret key required out of debug; secure cookies, HSTS, nosniff, `DENY`
  framing; `DEBUG` defaults off. Runs as a non-root container user.
- Resource limits: configurable request-body cap and a max-image-pixels guard.

### Added

- Assisted spot detection ("Auto-detect"): scikit-image Laplacian-of-Gaussian
  blobs + lane-profile `peak_local_max`, shape-gated to reject lines/labels.
- Optional "Plate Area" region to confine detection to the plate. An optional
  "ring marks" mode centres detections on ring outlines rather than filled area
  (toggling it re-runs Auto-detect). While a plate area is set, lines and spots
  are kept inside it — placement outside is blocked, dragging clamps to the
  edges, and redrawing the box clamps the lines / drops spots that fall outside.
- Stale-results banner: after a calculation, any annotation change (add/move/
  delete a spot, re-label, resize labels, redraw the plate area…) flags the
  results out of date — a banner shows, Calculate RF pulses, the annotated image
  dims, and export (CSV / Image / To ELN) is blocked until you recalculate, so a
  stale image can't be saved or pushed to the ELN.
- Rotate the image 90° (on a clean image, from the collapsed image-source bar)
  to correct orientation.
- Crop-to-plate view: zoom the work area to the plate box (view only — the
  saved image keeps its full original dimensions, including rotation).
- Annotation text-size control (10–500%): scales the result labels in both the
  live preview and the saved image, clamped client- and server-side.
- Undo / redo of annotation edits (add/move/delete lines & spots, Auto-detect,
  plate-area changes) via toolbar buttons and Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z).
- Export: four one-file actions — **download** CSV / Image, and (for
  ELN-sourced plates) **upload** CSV / Image to the ELN; the upload pair is
  hidden for local uploads and the download pair expands to fill.
- Light/dark theme toggle, persisted per browser.
- Touch and pen support via Pointer Events (drag, double-tap to delete).
- `/healthz` endpoint (reports version); `cleanup_media` management command.
- Playwright + Chromium end-to-end/regression suite (38 tests, incl. touch)
  and Django unit tests (63 tests, ~97% coverage of `config/`+`tlc/`).
- GitHub Actions and GitLab CI (lint, format, type-check, unit, e2e; image
  built only on release/tag). EXIF orientation handling for uploads.

### Changed

- Upgraded Django 3.2 → 5.2 LTS; dependencies managed with uv + `pyproject.toml`.
- Renamed the project/app layout (`config/` + `tlc/`); frontend assets extracted
  to static files with no external CDNs.
- Redesigned the single-page UI: collapsible image-source card, a three-ribbon
  annotation toolbar grouped by workflow (Bounds / Annotate / Labels + actions)
  with per-button tooltips and responsive label truncation, orientation-aware
  work area (columns/rows), a results table with row highlight + delete, a help
  drawer, and contextual hints.
- Reference lines are colour-coded (Min green, Max red, H-Line purple, V-Line
  blue) with a matching delete cross placed clear of the label.
- Frontend organised as native ES modules (`static/tlc/js/`, no bundler).
- Simplified deployment config: a typical ELN deployment needs only `SECRET_KEY`,
  `ALLOWED_HOSTS`, and `ELN_ORIGIN`. `CSRF_TRUSTED_ORIGINS` is derived from
  `ALLOWED_HOSTS`; the CSP `connect-src` and fetch allowlist from `ELN_ORIGIN`
  (all still overridable individually).
- Fixed click/annotation misalignment on CSS-scaled canvases (pointer mapping
  now uses the rendered rectangle); delete crosses track their line when the
  canvas is scaled.
- Local uploads now decode server-side correctly; output files use unique names.

### Removed

- Dead code and templates; unused `matplotlib`/`scikit-learn`; the admin, auth,
  sessions, and messages apps (no models; cookie-based CSRF). No database.

## [1.0.0] — original

The initial TLC RF calculator: browser annotation of a plate image with
server-side RF calculation and CSV/annotated-image export, and basic Chemotion
ELN load/save. Baseline prior to the v2 overhaul.

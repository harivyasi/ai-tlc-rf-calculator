# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [2.0.0] — Unreleased

The application is rebuilt on a new stack and interface. The analysis
workflow of version 1 -- the two ONNX models, spot search between the
baseline and the solvent front, line assignment by position, the Rf rule and
nearest-marker assignment -- is kept; a plate-by-plate check on 223 photos
gives identical detection results.

### Changed (breaking)

- New project layout and stack: Django 5.2 on Python 3.14 (`config/`,
  `tlc/`), dependencies managed with uv (`pyproject.toml`, `uv.lock`), a
  multi-stage Docker image served by Gunicorn and WhiteNoise. No database.
- New interface in plain JavaScript modules, with no external libraries or
  CDNs (Materialize CSS and SweetAlert2 are gone): a toolbar grouped by
  task, a collapsible image source, undo/redo, crop view, adjustable label
  size, touch and pen support, light and dark themes, and a warning when
  results are out of date.
- Endpoints: `/auto_detect_lines/` and `/auto_detect_spots/` become
  `POST /detect-lines/` and `POST /detect-spots/`. Detect Spots needs the
  baseline and solvent front (`minY`, `maxY`) and searches only between them
  (±12 px); a Plate Area also limits the x-range. Responses name the model
  (file, SHA-256, export date). Candidates need a confidence of at least
  0.25, the cutoff built into `spot_best.onnx` (it returns no lower scores).
- A spot is kept when **0 ≤ Rf ≤ 1**, tested before rounding; before, 0 and
  1 were excluded after rounding, so a spot on the baseline was dropped.
- Calculate RF refuses a baseline at or above the solvent front, and
  positions that are not numbers, with a message.
- Image limits: 25 MB per file (`MAX_IMAGE_MB`) and 64 megapixels
  (`MAX_IMAGE_MEGAPIXELS`), enough for phone photos of plates (13 MB, 50 MP);
  the page reads the file limit from the server.

### Added

- **Plate Area**: a box around the plate that confines detection, with
  lines and spots kept inside it.
- With a Plate Area set, **Detect Lines** searches only inside it, so a line
  is not put on the background of an uncropped photo.
- `tools/legacy_equivalence.py`: plate-by-plate comparison of detection with
  version 1 (223 plates, 4,941 spot candidates, identical).

### Changed

- Server sizing for the models: 2 Gunicorn workers x 4 threads, a 120 s
  timeout, `ONNX_INTRA_OP_THREADS` capping ONNX Runtime's threads; models
  load lazily, once per worker, behind a lock.

### Fixed

- The TLC-LineDet class ids were hard-coded the wrong way round; they are
  read from the model (no effect on results, which are assigned by position).
- Phone photos with an EXIF orientation are analysed the way the browser
  shows them; they used to be analysed sideways.

### Security

- CSRF protection on every POST (endpoints were `csrf_exempt`), a strict
  Content Security Policy, SSRF-guarded ELN image fetch (public hosts only,
  every redirect re-checked), `/serve-image/` confined to the media folder,
  secure cookies and HSTS in production, and a non-root container.

## [1.0.1] — 2026-05-31

Updated application code, README and user-guide images.

## [1.0.0] — 2026-05-30

First release: browser annotation of TLC plate images, ONNX-assisted line
and spot detection (TLC-LineDet, TLC-SpotDet), Rf calculation, CSV and
annotated-image export, and Chemotion ELN load and save.

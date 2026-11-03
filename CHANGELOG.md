# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

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

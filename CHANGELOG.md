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
- Optional "Plate Area" region to confine detection; it also suggests editable
  Min/Max reference lines from its edges.
- `/healthz` endpoint (reports version); `cleanup_media` management command.
- Playwright + Chromium end-to-end/regression suite and Django unit tests.
- GitHub Actions and GitLab CI (lint, format, type-check, unit, e2e; image
  built only on release/tag). EXIF orientation handling for uploads.

### Changed

- Upgraded Django 3.2 → 5.2 LTS; dependencies managed with uv + `pyproject.toml`.
- Renamed the project/app layout (`config/` + `tlc/`); frontend assets extracted
  to static files with no external CDNs.
- Local uploads now decode server-side correctly; output files use unique names.

### Removed

- Dead code and templates; unused `matplotlib`/`scikit-learn`; the admin, auth,
  sessions, and messages apps (no models; cookie-based CSRF). No database.

## [1.0.0] — original

The initial TLC RF calculator: browser annotation of a plate image with
server-side RF calculation and CSV/annotated-image export, and basic Chemotion
ELN load/save. Baseline prior to the v2 overhaul.

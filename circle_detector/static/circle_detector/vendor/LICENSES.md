# Vendored third-party assets

These files were previously loaded from public CDNs. They are vendored so the
application works on an isolated network and can run under a Content Security
Policy that permits no external origins.

| Project | Asset | Version | Upstream | Licence |
|---|---|---|---|---|
| Materialize | `materialize.min.css`, `materialize.min.js` | 1.0.0 | cdnjs.cloudflare.com/ajax/libs/materialize | MIT |
| SweetAlert2 | `sweetalert2.all.min.js` | 11.14.5 | cdn.jsdelivr.net/npm/sweetalert2 | MIT |
| Material Icons | `material-icons.css` + `fonts/material-icons-*.woff2` | v145 | fonts.googleapis.com/icon?family=Material+Icons | Apache-2.0 |
| Inter | `inter.css` + `fonts/inter-*.woff2` | v20 | fonts.googleapis.com/css2?family=Inter | SIL Open Font License 1.1 |

## Notes

**Inter** upstream serves 35 `@font-face` blocks: five weights (300-700) across
seven unicode subsets. Only `latin` and `latin-ext` are vendored, which keeps
all five weights while dropping cyrillic, cyrillic-ext, greek, greek-ext and
vietnamese. Inter is a variable font, so each subset is a single file shared by
every weight. Restore the other subsets from upstream if the UI is localised
into those scripts.

**Material Icons** is the full ligature font, so icon names in the markup
(`<i class="material-icons">save</i>`) continue to resolve.

Licence texts: MIT and Apache-2.0 are the standard texts; the SIL OFL 1.1
applies to Inter as published on Google Fonts.

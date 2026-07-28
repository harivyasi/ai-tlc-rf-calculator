# TLC RF Calculator

A web tool for computing **retention factor (RF)** values from **Thin Layer
Chromatography (TLC)** plate images.

Load a photo of a TLC plate — from your computer or straight from an Electronic
Lab Notebook (ELN) — mark the reference lines and spots in your browser, and the
tool computes the RF for each spot, produces an annotated image, and exports the
results as CSV (or sends them back to the ELN).

RF is the distance a spot travelled divided by the distance the solvent front
travelled, both measured from the baseline:

```text
RF = (distance from baseline to spot) / (distance from baseline to solvent front)
```

## Using the tool

1. **Load a plate image.** Choose **Upload local** (drag-and-drop or pick a
   JPG/PNG under 10 MB) or **From ELN** if the tool was opened from your ELN.
2. **(Recommended) Draw a Plate Area.** Click **Plate Area** and drag a box
   around the plate. This makes **Auto-detect** far more reliable — it ignores
   everything outside the box. (It does *not* place the reference lines.)
3. **Mark the reference lines.** Click **Min Line** and click on the **baseline**
   (where the samples were spotted); click **Max Line** and click on the
   **solvent front** (how far the solvent ran).
4. **Mark the spots.** Click **Auto-detect** to find them automatically (best
   inside a Plate Area — review and adjust the suggestions), or **Add Spots** to
   place them by hand. Drag to reposition; double-click (double-tap on touch) to
   delete. Mouse, touch, and pen all work.
5. **(Optional) Label lanes and compounds** with **V-Line** / **H-Line**.
6. **Click Calculate RF.** The results table and the annotated image appear.
   Export as **CSV** or **Image**, or send back **To ELN**.

### Handy features

- **Undo / redo** every edit — the toolbar buttons or **Ctrl+Z** / **Ctrl+Y**
  (**Ctrl+Shift+Z**).
- **Auto-detect options:** tick **ring marks** for hand-drawn rings (centres on
  the ring outline). Toggling it re-runs detection.
- **Crop view** zooms the work area to the plate box for easier marking — it only
  changes what you see; the saved image keeps its full original size.
- **Rotate** a freshly loaded image 90° to fix its orientation (before you
  annotate).
- **Text size** (10–500%) scales the labels on the preview and the saved image.
- **Light / dark theme** toggle (top-right), remembered per browser.
- **Stale-results guard:** if you change anything after calculating, a banner
  reminds you to recalculate and export stays disabled — so you never save an
  out-of-date image.

### Tips for good results

- Use a well-lit, high-resolution, roughly straight-on photo of the plate.
- Draw the Plate Area first — it makes Auto-detect much more reliable.
- Put **Min** on the baseline and **Max** on the solvent front; RF values fall
  between 0 and 1 (spots outside that range are ignored).
- The built-in **Help** button (toolbar) has a quick step-by-step reminder.

## For developers & deployers

This is an open-source Django + vanilla-JS app (no database). See:

- **[docs/architecture.md](docs/architecture.md)** — architecture, design
  decisions, module map, security model, testing, build/CI, and how to run it
  locally or deploy it (Docker, configuration, environment variables).
- **[docs/eln-integration.md](docs/eln-integration.md)** — the Chemotion ELN
  contract (the signed URL, GET/POST endpoint, CORS/CSP requirements).

Quick start:

```bash
uv sync
DEBUG=True uv run python manage.py runserver   # then open http://localhost:8000/
```

## License

MIT

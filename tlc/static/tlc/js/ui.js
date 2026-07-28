/*
 * Contextual UI state: the annotated-image heading, the collapsible image
 * source card, the help drawer, the work-area layout (rows vs. columns), the
 * hint bar, and the enabled/disabled state of the Calculate and Rotate buttons.
 *
 * Reads state and the DOM only — no canvas imports, so it sits below canvas.js
 * in the dependency graph.
 */

"use strict";

import { state } from "./state.js";
import { canRedo, canUndo } from "./history.js";

// Annotation text-size multiplier from the toolbar input, as a fraction
// (100% -> 1.0), clamped to [0.1, 5.0] to match the server-side clamp.
export function textScale() {
    const el = document.getElementById("text-scale");
    const pct = Number(el ? el.value : 100);
    // Empty / invalid / non-positive (e.g. a cleared field) -> default 100%.
    if (!Number.isFinite(pct) || pct <= 0) return 1;
    return Math.max(0.1, Math.min(5, pct / 100));
}

export function hasImage() {
    const img = state.image;
    return img && img.src && (img.complete ? img.naturalWidth > 0 : true);
}

export function updateAnnotatedHeading(stateName) {
    const heading = document.getElementById("annotated-heading");
    const annotated = document.getElementById("annotated-image");
    const placeholder = document.getElementById("result-placeholder");
    if (!heading) return;
    if (stateName === "annotated") {
        heading.textContent = "Annotated image";
        annotated.style.display = "block";
        if (placeholder) placeholder.style.display = "none";
    } else {
        heading.textContent = "Result";
        annotated.style.display = "none";
        if (placeholder) placeholder.style.display = "";
    }
}

export function toggleImageSource() {
    const body = document.getElementById("source-body");
    const change = document.getElementById("source-change");
    const collapsed = body.classList.toggle("hidden");
    if (change) change.setAttribute("aria-expanded", String(!collapsed));
}

export function collapseImageSource(name) {
    document.getElementById("source-body").classList.add("hidden");
    document.getElementById("source-summary").classList.add("shown");
    const change = document.getElementById("source-change");
    if (change) change.setAttribute("aria-expanded", "false");
    document.getElementById("source-name").textContent = name;
}

export function toggleHelp() {
    document.getElementById("help-drawer").classList.toggle("open");
}

export function updateWorkAreaLayout() {
    const area = document.getElementById("work-area");
    if (!area || !state.image) return;
    // Use the displayed (rotation-adjusted) dimensions.
    const swap = state.rotation === 90 || state.rotation === 270;
    const w = swap ? state.image.naturalHeight : state.image.naturalWidth;
    const h = swap ? state.image.naturalWidth : state.image.naturalHeight;
    // Portrait (tall) -> side-by-side columns; landscape (wide) -> stacked rows.
    const landscape = w > h;
    area.classList.toggle("layout-rows", landscape);
    area.classList.toggle("layout-columns", !landscape);
}

export function updateHint() {
    const hint = document.getElementById("hint");
    if (!hint) return;
    let msg = "";
    if (!hasImage()) {
        msg = "";
    } else if (!state.plateRegion) {
        msg = "Tip: click Plate Area and drag a box around the plate — this makes Auto-detect far more reliable.";
    } else if (state.spots.length === 0) {
        msg = "Now click Auto-detect to find spots automatically (or Add Spots to place them yourself).";
    } else if (!state.lines.some((l) => l.type === "min") || !state.lines.some((l) => l.type === "max")) {
        msg = "Add Min and Max reference lines, then Calculate RF.";
    } else {
        msg = "Ready — click Calculate RF.";
    }
    hint.textContent = msg;
    hint.classList.toggle("show", msg !== "");
}

export function updateCalculateEnabled() {
    const btn = document.getElementById("calculate-btn");
    if (!btn) return;
    const ready =
        hasImage() &&
        state.lines.some((l) => l.type === "min") &&
        state.lines.some((l) => l.type === "max") &&
        state.spots.length > 0;
    btn.disabled = !ready;
}

// Rotation is only allowed on a clean image (no annotations to invalidate),
// so the reference-line / RF axis never has to change.
export function hasAnnotations() {
    return state.spots.length > 0 || state.lines.length > 0 || !!state.plateRegion;
}

export function updateRotateEnabled() {
    const locked = hasAnnotations();
    document
        .querySelectorAll('[data-action="rotateCW"], [data-action="rotateCCW"]')
        .forEach((btn) => {
            btn.disabled = locked || !hasImage();
        });
}

// --- Stale-results handling -----------------------------------------------
// The results table + annotated image are a snapshot from the last Calculate RF.
// Any annotation change afterwards makes them out of date: we keep them on
// screen but flag them (banner + pulsing Calculate RF) and block export until
// the user recalculates, so a stale image can't be saved / pushed to the ELN.

function setExportDisabled(disabled) {
    document.querySelectorAll("#export-row .lab-btn").forEach((b) => {
        b.disabled = disabled;
    });
}

// The two "upload to ELN" export buttons only make sense for an ELN-sourced
// plate; for a local upload they're hidden and the two download buttons expand
// to fill the row (flex: 1).
export function updateExportButtons() {
    document.querySelectorAll("#export-row .export-eln").forEach((b) => {
        b.classList.toggle("hidden", state.isLocalImage);
    });
}

export function markResultsStale() {
    if (!state.hasResults) return; // nothing calculated yet; writes below are idempotent
    const banner = document.getElementById("stale-banner");
    if (banner) banner.classList.add("show");
    const annotated = document.getElementById("annotated-image");
    if (annotated) annotated.classList.add("stale");
    const calc = document.getElementById("calculate-btn");
    if (calc) calc.classList.add("pulse");
    setExportDisabled(true);
}

// Called on a fresh calculation (results now current) and on reset/new image
// (no results). `hasResults` is set by the caller.
export function clearResultsStale() {
    const banner = document.getElementById("stale-banner");
    if (banner) banner.classList.remove("show");
    const annotated = document.getElementById("annotated-image");
    if (annotated) annotated.classList.remove("stale");
    const calc = document.getElementById("calculate-btn");
    if (calc) calc.classList.remove("pulse");
    setExportDisabled(!state.hasResults); // export enabled only with fresh results
}

function refreshUndoRedo() {
    const undo = document.getElementById("undo-btn");
    const redo = document.getElementById("redo-btn");
    if (undo) undo.disabled = !canUndo();
    if (redo) redo.disabled = !canRedo();
}

// Refresh contextual UI after any annotation change.
export function refreshUI() {
    updateHint();
    updateCalculateEnabled();
    updateRotateEnabled();
    refreshUndoRedo();
}

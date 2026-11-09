/*
 * Server round-trips and results: building the image payload, RF calculation,
 * candidate line and spot detection, the results table (with row highlight/delete), CSV
 * export, and saving image/CSV back to the ELN.
 */

"use strict";

import { elnUrl, state } from "./state.js";
import { getCookie, hideOverlay, notify, showOverlay } from "./ui-kit.js";
import { clearResultsStale, hasImage, textScale, updateAnnotatedHeading } from "./ui.js";
import { addSpot } from "./annotations.js";
import { clamp } from "./geometry.js";
import { commitChange, redrawCanvas } from "./canvas.js";
import { commitHistory } from "./history.js";

// --- Image payload (shared by calculate + detection) ----------------------
// Resolves to the image fields of a request. A local image is referred to by
// the name it was uploaded under (sources.js); only if that upload failed is
// the whole image sent inline, as before.
export function getImagePayload() {
    const extras = (payload) => {
        payload.rotation = state.rotation; // backend re-orients to match the displayed image
        payload.textScale = textScale(); // label text-size multiplier for the saved image
        const full = document.getElementById("label-full");
        payload.labelMode = full && full.checked ? "full" : "numbers";
        return payload;
    };
    if (state.isLocalImage) {
        return Promise.resolve(state.localImageUpload).then((stored) =>
            extras(
                stored
                    ? { imageSrc: stored, isLocalImage: true, imageData: null }
                    : {
                          imageSrc: window.currentImageFileName || "local_upload.png",
                          isLocalImage: true,
                          imageData: state.image.src,
                      },
            ),
        );
    }
    let payload;
    try {
        const parsed = new URL(state.image.src);
        const filePath = parsed.searchParams.get("file_path");
        payload = {
            imageSrc: decodeURIComponent(filePath).split("/").pop(),
            isLocalImage: false,
            imageData: null,
        };
    } catch (err) {
        payload = { imageSrc: "unknown_image.png", isLocalImage: false, imageData: null };
    }
    return Promise.resolve(extras(payload));
}

// --- RF calculation -------------------------------------------------------
export function calculateRF() {
    const minLine = state.lines.find((l) => l.type === "min");
    const maxLine = state.lines.find((l) => l.type === "max");
    if (!minLine || !maxLine) {
        notify('Please add "min" and "max" lines before calculating RF.', "warning");
        return;
    }
    if (state.spots.length === 0) {
        notify("Please add spots before calculating RF.", "warning");
        return;
    }

    showOverlay("Calculating RF values…");
    getImagePayload()
        .then((image) =>
            postJson("/calculate_rf/", Object.assign({ lines: state.lines, spots: state.spots }, image)),
        )
        .then((result) => {
            hideOverlay();
            if (result.rf_values && result.rf_values.length > 0) {
                populateTable(result.rf_values);
                const annotated = document.getElementById("annotated-image");
                if (result.annotated_image_url) {
                    annotated.src = result.annotated_image_url + "?t=" + Date.now();
                    updateAnnotatedHeading("annotated");
                }
                state.csvUrl = result.csv_url || null;
                state.hasResults = true; // results are now current
                clearResultsStale();
                notify("RF calculation complete: " + result.rf_values.length + " spot(s).", "success");
            } else {
                notify("No RF values calculated. Check your annotations.", "warning");
            }
        })
        .catch((error) => {
            hideOverlay();
            console.error("Error calculating RF:", error);
            notify(error.message || "Failed to calculate RF values.", "error");
        });
}

// --- Candidate detection (TLC-SpotDet / TLC-LineDet) ---------------------
// POST JSON; on failure, reject with the server's user-facing ``error`` when
// it sent one (the 400s explain what to fix), else a generic HTTP status.
function postJson(url, payload) {
    return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
        body: JSON.stringify(payload),
    }).then((response) =>
        response
            .json()
            .catch(() => ({}))
            .then((body) => {
                // 400s carry a user-facing reason; anything else is generic.
                if (!response.ok) throw new Error(body.error || "HTTP " + response.status);
                return body;
            }),
    );
}

// Spots are searched only between the baseline and the solvent front (SI §4),
// so both lines must exist first.
export function autoDetectSpots() {
    if (!hasImage()) {
        notify("Load an image first.", "warning");
        return;
    }
    const minLine = state.lines.find((l) => l.type === "min");
    const maxLine = state.lines.find((l) => l.type === "max");
    if (!minLine || !maxLine) {
        notify("Place the Min and Max lines first (or click Detect Lines).", "warning");
        return;
    }
    showOverlay("Detecting spots…");
    getImagePayload()
        .then((payload) => {
            payload.minY = minLine.y;
            payload.maxY = maxLine.y;
            if (state.plateRegion) payload.region = state.plateRegion;
            return postJson("/detect-spots/", payload);
        })
        .then((result) => {
            hideOverlay();
            let added = 0;
            (result.spots || []).forEach((s) => {
                if (addSpot(s.x, s.y, true)) added++;
            });
            if (added > 0) {
                commitChange(); // one undo entry for the whole detection batch
                notify("Detected " + added + " spot(s). Review and adjust as needed.", "success");
            } else {
                redrawCanvas();
                notify("No new spots detected — add them manually.", "info");
            }
        })
        .catch((error) => {
            hideOverlay();
            console.error("Error detecting spots:", error);
            notify(error.message || "Spot detection failed.", "error");
        });
}

// Place (or move) the Min and Max lines where TLC-LineDet puts them. The server
// assigns them by position — lower = baseline — and estimates one line at
// 10 % / 90 % of the height when only the other was found.
export function autoDetectLines() {
    if (!hasImage()) {
        notify("Load an image first.", "warning");
        return;
    }
    showOverlay("Detecting reference lines…");
    getImagePayload()
        .then((payload) => {
            if (state.plateRegion) payload.region = state.plateRegion;
            return postJson("/detect-lines/", payload);
        })
        .then((result) => {
            hideOverlay();
            const r = state.plateRegion;
            const place = (type, y) => {
                // Lines must stay inside a plate area, as when placed by hand.
                const yy = r ? clamp(y, r.y, r.y + r.height) : y;
                const existing = state.lines.find((l) => l.type === type);
                if (existing) existing.y = yy;
                else state.lines.push({ id: Date.now() + state.lineSeq++, type, y: yy, text: type });
            };
            place("min", result.minY);
            place("max", result.maxY);
            commitChange(); // both lines are one undo step
            if (result.estimated) {
                const which = result.estimated === "min" ? "Min (baseline)" : "Max (solvent front)";
                notify("Only one line was found; " + which + " is an estimate — drag it into place.", "warning");
            } else {
                notify("Placed Min and Max lines. Review and drag to adjust.", "success");
            }
        })
        .catch((error) => {
            hideOverlay();
            console.error("Error detecting lines:", error);
            notify(error.message || "Line detection failed.", "error");
        });
}

// Rows arrive sorted (by lane label, then Rf) and numbered by the server; the
// number matches the one on the annotated image and in the CSV.
export function populateTable(rfValues) {
    const table = document.getElementById("values-table");
    const tbody = table.getElementsByTagName("tbody")[0];
    tbody.innerHTML = "";
    state.highlightedSpot = null;
    // Type columns appear only when some marker has a type.
    table.classList.toggle("no-lane-type", !rfValues.some((v) => v.solvent_type));
    table.classList.toggle("no-compound-type", !rfValues.some((v) => v.compound_type));
    rfValues.forEach((value, index) => {
        const row = tbody.insertRow();
        if (typeof value.x === "number" && typeof value.y === "number") {
            row.dataset.x = value.x;
            row.dataset.y = value.y;
            row.classList.add("clickable");
            row.addEventListener("click", () => highlightSpotFromRow(row));
        }
        const cells = [
            ["col-n", value.n ?? index + 1],
            ["", value.solvent || ""], // an unmarked lane is blank
            ["col-lane-type", value.solvent_type || ""],
            ["", value.compound || ""], // "Unknown" from the server when unmarked
            ["col-compound-type", value.compound_type || ""],
            ["col-rf", typeof value.rf === "number" ? value.rf.toFixed(2) : ""],
        ];
        cells.forEach(([cls, text]) => {
            const cell = row.insertCell();
            if (cls) cell.className = cls;
            cell.textContent = text;
        });
        const actions = row.insertCell();
        const del = document.createElement("button");
        del.type = "button";
        del.className = "row-del";
        del.setAttribute("aria-label", "Delete spot " + (value.n ?? index + 1));
        del.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-close"/></svg>';
        del.addEventListener("click", (e) => {
            e.stopPropagation();
            deleteResultRow(row);
        });
        actions.appendChild(del);
    });
}

function highlightSpotFromRow(row) {
    if (row.dataset.x === undefined) return;
    state.highlightedSpot = { x: Number(row.dataset.x), y: Number(row.dataset.y) };
    document
        .querySelectorAll("#values-table tbody tr.active")
        .forEach((r) => r.classList.remove("active"));
    row.classList.add("active");
    redrawCanvas();
}

function deleteResultRow(row) {
    if (row.dataset.x !== undefined) {
        const rx = Number(row.dataset.x);
        const ry = Number(row.dataset.y);
        state.spots = state.spots.filter((s) => Math.abs(s.x - rx) > 1 || Math.abs(s.y - ry) > 1);
        if (
            state.highlightedSpot &&
            Math.abs(state.highlightedSpot.x - rx) < 2 &&
            Math.abs(state.highlightedSpot.y - ry) < 2
        ) {
            state.highlightedSpot = null;
        }
    }
    row.remove();

    // Deleting the spot is an undoable edit. If none remain, clear the results
    // entirely; otherwise commitChange() records it and flags the results stale.
    if (state.spots.length > 0) {
        commitChange();
    } else {
        const annotated = document.getElementById("annotated-image");
        if (annotated) annotated.removeAttribute("src");
        state.hasResults = false;
        state.csvUrl = null;
        clearResultsStale();
        updateAnnotatedHeading("editing");
        commitHistory();
        redrawCanvas();
    }
}

// --- CSV export -----------------------------------------------------------
// The CSV is written by the server with the calculation (tlc/results.py), so
// it always matches the table and the numbered image. Export is disabled while
// results are stale, so the stored file is never out of date when used.
export function saveCSV() {
    if (!state.csvUrl) {
        notify("Calculate RF values first.", "warning");
        return;
    }
    const link = document.createElement("a");
    link.href = state.csvUrl;
    link.download = "rf_values.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    notify("CSV downloaded.", "success");
}

// --- Save back to the ELN -------------------------------------------------
// The name the file gets in Chemotion, e.g. "TLC_Rf_2026-11-09_14-32.csv".
// Chemotion uses `attachmentName` as given; without it, it names the file
// "<app name>-<upload name>".
export function elnAttachmentName(kind, ext, date = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");
    const stamp =
        date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
        "_" + pad(date.getHours()) + "-" + pad(date.getMinutes());
    return "TLC_" + kind + "_" + stamp + "." + ext;
}

// Sent cross-origin to the ELN's token URL. No CSRF header: the token is ours,
// means nothing to the ELN, and a custom header would force a CORS preflight.
function postToEln(blob, kind, ext) {
    if (!elnUrl) {
        notify("No ELN target configured.", "warning");
        return;
    }
    const name = elnAttachmentName(kind, ext);
    const formData = new FormData();
    formData.append("file", blob, name);
    formData.append("attachmentName", name);
    fetch(elnUrl, { method: "POST", body: formData })
        .then((response) => {
            if (!response.ok) throw new Error("HTTP " + response.status);
        })
        .then(() => notify('Saved to ELN as "' + name + '".', "success"))
        .catch((error) => {
            console.error("Error saving to ELN:", error);
            notify("Failed to save to ELN.", "error");
        });
}

// Download the annotated image locally.
export function saveImage() {
    const annotated = document.getElementById("annotated-image");
    if (!annotated || !annotated.src) {
        notify("Calculate RF values first.", "warning");
        return;
    }
    const link = document.createElement("a");
    link.download = "annotated_" + (window.currentImageFileName || "tlc_image.png");
    link.href = annotated.src;
    link.click();
    notify("Annotated image downloaded.", "success");
}

// Upload the annotated image to the ELN (re-encoded from the full-res result).
export function imageToEln() {
    const annotated = document.getElementById("annotated-image");
    if (!annotated || !annotated.src) {
        notify("Annotated image not found. Calculate RF values first.", "error");
        return;
    }
    const sourceImage = new Image();
    sourceImage.crossOrigin = "anonymous";
    sourceImage.onload = function () {
        const exportCanvas = document.createElement("canvas");
        exportCanvas.width = sourceImage.naturalWidth;
        exportCanvas.height = sourceImage.naturalHeight;
        exportCanvas.getContext("2d").drawImage(sourceImage, 0, 0);
        exportCanvas.toBlob((blob) => postToEln(blob, "annotated", "png"), "image/png");
    };
    sourceImage.onerror = () => notify("Failed to load annotated image.", "error");
    sourceImage.src = annotated.src;
}

// Upload the results CSV to the ELN.
export function csvToEln() {
    if (!state.csvUrl) {
        notify("No CSV data to save. Calculate RF values first.", "warning");
        return;
    }
    fetch(state.csvUrl)
        .then((response) => {
            if (!response.ok) throw new Error("HTTP " + response.status);
            return response.blob();
        })
        .then((blob) => postToEln(blob, "Rf", "csv"))
        .catch(() => notify("Failed to load the CSV.", "error"));
}

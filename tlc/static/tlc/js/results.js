/*
 * Server round-trips and results: building the image payload, RF calculation,
 * candidate line and spot detection, the results table (with row highlight/delete), CSV
 * export, and saving image/CSV back to the ELN.
 */

"use strict";

import { elnUrl, state } from "./state.js";
import { getCookie, hideOverlay, notify, showOverlay, timestampedName } from "./ui-kit.js";
import { clearResultsStale, hasImage, textScale, updateAnnotatedHeading } from "./ui.js";
import { addSpot } from "./annotations.js";
import { clamp } from "./geometry.js";
import { commitChange, redrawCanvas } from "./canvas.js";
import { commitHistory } from "./history.js";

// --- Image payload (shared by calculate + detection) ----------------------
export function getImagePayload() {
    let payload;
    if (state.isLocalImage) {
        payload = {
            imageSrc: window.currentImageFileName || "local_upload.png",
            isLocalImage: true,
            imageData: state.image.src,
        };
    } else {
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
    }
    payload.rotation = state.rotation; // backend re-orients to match the displayed image
    payload.textScale = textScale(); // label text-size multiplier for the saved image
    return payload;
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

    const data = Object.assign({ lines: state.lines, spots: state.spots }, getImagePayload());
    showOverlay("Calculating RF values…");

    fetch("/calculate_rf/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
        body: JSON.stringify(data),
    })
        .then((response) => {
            if (!response.ok) throw new Error("HTTP " + response.status);
            return response.json();
        })
        .then((result) => {
            hideOverlay();
            if (result.rf_values && result.rf_values.length > 0) {
                populateTable(result.rf_values);
                const annotated = document.getElementById("annotated-image");
                if (result.annotated_image_url) {
                    annotated.src = result.annotated_image_url + "?t=" + Date.now();
                    updateAnnotatedHeading("annotated");
                }
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
            notify("Failed to calculate RF values.", "error");
        });
}

// --- Candidate detection (TLC-SpotDet / TLC-LineDet) ---------------------
function postDetection(url, payload) {
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
    const payload = getImagePayload();
    payload.minY = minLine.y;
    payload.maxY = maxLine.y;
    if (state.plateRegion) payload.region = state.plateRegion;
    postDetection("/detect-spots/", payload)
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
    postDetection("/detect-lines/", getImagePayload())
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

export function populateTable(rfValues) {
    const tbody = document.getElementById("values-table").getElementsByTagName("tbody")[0];
    tbody.innerHTML = "";
    state.highlightedSpot = null;
    rfValues.forEach((value, index) => {
        const row = tbody.insertRow();
        if (typeof value.x === "number" && typeof value.y === "number") {
            row.dataset.x = value.x;
            row.dataset.y = value.y;
            row.classList.add("clickable");
            row.addEventListener("click", () => highlightSpotFromRow(row));
        }
        // Lane (X) is optional — shown blank when unmarked; compound (Y) is
        // "Unknown" from the server when unmarked. Kept consistent with the
        // annotated image and the CSV.
        row.insertCell(0).textContent = value.solvent || "";
        row.insertCell(1).textContent = value.compound || "";
        row.insertCell(2).textContent = typeof value.rf !== "undefined" ? value.rf : value.rf_value;
        const actions = row.insertCell(3);
        const del = document.createElement("button");
        del.type = "button";
        del.className = "row-del";
        del.setAttribute("aria-label", "Delete this spot");
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
        clearResultsStale();
        updateAnnotatedHeading("editing");
        commitHistory();
        redrawCanvas();
    }
}

// --- CSV export -----------------------------------------------------------
function tableToCsvRows() {
    const table = document.getElementById("values-table");
    return Array.from(table.rows)
        .slice(1)
        .map((row) => Array.from(row.cells).map((c) => c.textContent.trim()));
}

function buildCsvContent() {
    let csv = "Measurement type;thin layer chromatography (TLC);CHMO:0001007\n\n";
    csv += "mixture_label (X axis),substance_label (Y axis),RF value\n";
    const inline = [];
    tableToCsvRows().forEach((cols, i) => {
        csv += cols[0] + "," + cols[1] + "," + cols[2] + "\n";
        inline.push(cols[2] + " (" + cols[1] + ")[h" + (i + 1) + "]");
    });
    csv += "\nDetails:\nTLC (solvents); Rf (UV-Vis in nm, staining) = " + inline.join(", ") + ".\n";
    return csv;
}

export function saveCSV() {
    const uri = encodeURI("data:text/csv;charset=utf-8," + buildCsvContent());
    const link = document.createElement("a");
    link.href = uri;
    link.download = "rf_values.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    notify("CSV downloaded.", "success");
}

// --- Save back to the ELN -------------------------------------------------
function postToEln(blob, filename) {
    if (!elnUrl) {
        notify("No ELN target configured.", "warning");
        return;
    }
    const formData = new FormData();
    formData.append("file", blob, filename);
    fetch(elnUrl, {
        method: "POST",
        body: formData,
        headers: { "X-CSRFToken": getCookie("csrftoken") },
    })
        .then((response) => response.json())
        .then(() => notify("Saved to ELN.", "success"))
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
        exportCanvas.toBlob((blob) => postToEln(blob, timestampedName("png")), "image/png");
    };
    sourceImage.onerror = () => notify("Failed to load annotated image.", "error");
    sourceImage.src = annotated.src;
}

// Upload the results CSV to the ELN.
export function csvToEln() {
    const csv = buildCsvContent();
    if (!csv) {
        notify("No CSV data to save.", "warning");
        return;
    }
    postToEln(new Blob([csv], { type: "text/csv;charset=utf-8;" }), timestampedName("csv"));
}

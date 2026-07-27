/*
 * TLC RF Calculator — annotation UI.
 *
 * Handles loading a TLC plate image (local upload or ELN URL), annotating it on
 * a canvas (min/max reference lines, vertical/horizontal label lines, spots),
 * assisted spot detection, RF calculation, and exporting results.
 *
 * Self-contained: no external libraries. Toasts and dialogs are implemented
 * locally (see notify/showOverlay).
 */

"use strict";

// --- State ----------------------------------------------------------------
let image = new Image();

const pageUrl = new URL(window.location.href);
const encodedUrl = pageUrl.searchParams.get("url");
// Chemotion passes one signed URL (via ?url=) that serves the source image on
// GET and accepts the annotated results on POST — the same endpoint for both.
const elnUrl = encodedUrl ? decodeURIComponent(encodedUrl) : null;

let canvas;
let ctx;

let lines = [];
let spots = [];
let spotSeq = 0;
let lineSeq = 0;
let rotation = 0; // clockwise degrees the image is displayed at (0/90/180/270)

let currentLine = null;
let isHorizontal = true;
let lineType = "";
let addingSpots = false;

let isLocalImage = false;

// Drag state.
let draggingSpot = null;
let draggingLine = null;
let isDragging = false;
let dragOffsetX = 0;
let dragOffsetY = 0;
let lineDragOffsetX = 0;
let lineDragOffsetY = 0;
let justDragged = false;

// Optional plate region-of-interest that constrains auto-detect.
let plateRegion = null; // {x, y, width, height} in original-image coordinates
let selectingRegion = false; // the "Plate Area" tool is armed
let draggingRegion = false; // a region drag is in progress
let regionStart = null; // {x, y} in display coordinates
let regionCurrent = null; // {x, y} in display coordinates
let highlightedSpot = null; // {x, y} of a result row highlighted on the canvas

// --- Small UI helpers (replace Materialize toast / SweetAlert) ------------
function notify(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = "toast toast-" + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.classList.add("toast-hide"), 3000);
    setTimeout(() => toast.remove(), 3400);
}

function showOverlay(text) {
    let overlay = document.getElementById("busy-overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "busy-overlay";
        overlay.innerHTML = '<div class="spinner"></div><p></p>';
        document.body.appendChild(overlay);
    }
    overlay.querySelector("p").textContent = text || "Working…";
    overlay.style.display = "flex";
}

function hideOverlay() {
    const overlay = document.getElementById("busy-overlay");
    if (overlay) overlay.style.display = "none";
}

// --- Theme (light/dark toggle, overrides the OS preference; persisted) -----
const THEME_KEY = "tlc-theme";

function currentTheme() {
    const explicit = document.documentElement.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
}

function refreshThemeToggle() {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    const isDark = currentTheme() === "dark";
    // Show a sun in dark mode (click -> light) and a moon in light mode.
    button.querySelector("use").setAttribute("href", isDark ? "#i-sun" : "#i-moon");
    const label = isDark ? "Switch to light theme" : "Switch to dark theme";
    button.setAttribute("aria-label", label);
    button.title = label;
}

function toggleTheme() {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
        localStorage.setItem(THEME_KEY, next);
    } catch (err) {
        /* localStorage unavailable (private mode) — session-only theme. */
    }
    refreshThemeToggle();
}

function initTheme() {
    try {
        const saved = localStorage.getItem(THEME_KEY);
        if (saved === "light" || saved === "dark") {
            document.documentElement.setAttribute("data-theme", saved);
        }
    } catch (err) {
        /* ignore */
    }
    refreshThemeToggle();
}

function getCookie(name) {
    const match = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return match ? decodeURIComponent(match.pop()) : null;
}

function timestampedName(ext) {
    const dt = new Date()
        .toLocaleString("en-GB", {
            year: "2-digit", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
        })
        .replace(/[/,: ]/g, "");
    return "_" + dt + "." + ext;
}

// --- Line-type predicate (was repeated inline throughout) -----------------
function isHorizontalLine(line) {
    return line.type === "horizontal" || line.type === "min" || line.type === "max";
}

// --- Canvas drawing -------------------------------------------------------
// Draw an image into the canvas frame applying the current clockwise rotation.
function drawImageRotated(context, img, rot, w, h) {
    context.save();
    if (rot === 90) {
        context.translate(w, 0);
        context.rotate(Math.PI / 2);
        context.drawImage(img, 0, 0, h, w);
    } else if (rot === 180) {
        context.translate(w, h);
        context.rotate(Math.PI);
        context.drawImage(img, 0, 0, w, h);
    } else if (rot === 270) {
        context.translate(0, h);
        context.rotate(-Math.PI / 2);
        context.drawImage(img, 0, 0, h, w);
    } else {
        context.drawImage(img, 0, 0, w, h);
    }
    context.restore();
}

function fitAndDraw(img) {
    const maxHeight = 500;
    const natW0 = img.naturalWidth || img.width;
    const natH0 = img.naturalHeight || img.height;
    // For 90/270 the displayed image's width/height are swapped.
    const swap = rotation === 90 || rotation === 270;
    const naturalW = swap ? natH0 : natW0;
    const naturalH = swap ? natW0 : natH0;
    let dispW = naturalW;
    let dispH = naturalH;
    if (naturalH > maxHeight) {
        dispW = Math.round((maxHeight * naturalW) / naturalH);
        dispH = maxHeight;
    }
    canvas.width = dispW;
    canvas.height = dispH;
    drawImageRotated(ctx, img, rotation, dispW, dispH);
    window.scaleFactor = naturalH / dispH;

    const annotated = document.getElementById("annotated-image");
    annotated.style.width = dispW + "px";
    annotated.style.height = dispH + "px";

    updateWorkAreaLayout();
    document.body.dataset.imageReady = "1"; // readiness signal (used by tests)
}

image.onload = function () {
    fitAndDraw(image);
};

function redrawCanvas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawImageRotated(ctx, image, rotation, canvas.width, canvas.height);

    ctx.font = "16px sans-serif";
    ctx.lineWidth = 2;

    // Lines.
    ctx.strokeStyle = "red";
    ctx.fillStyle = "red";
    lines.forEach((line) => {
        ctx.beginPath();
        if (isHorizontalLine(line)) {
            const y = line.y / window.scaleFactor;
            ctx.moveTo(0, y);
            ctx.lineTo(canvas.width, y);
            ctx.stroke();
            ctx.fillText(line.text, 10, y - 5);
        } else {
            const x = line.x / window.scaleFactor;
            ctx.moveTo(x, 0);
            ctx.lineTo(x, canvas.height);
            ctx.stroke();
            ctx.fillText(line.text, x + 5, 10);
        }
    });

    // Rebuild delete icons.
    document.querySelectorAll(".delete-icon").forEach((el) => el.remove());
    lines.forEach((line) => {
        if (isHorizontalLine(line)) {
            addDeleteIcon(line, 10, line.y / window.scaleFactor);
        } else {
            addDeleteIcon(line, line.x / window.scaleFactor, 10);
        }
    });

    // Spots.
    ctx.strokeStyle = "orange";
    spots.forEach((spot) => {
        ctx.beginPath();
        ctx.arc(spot.x / window.scaleFactor, spot.y / window.scaleFactor, 10, 0, 2 * Math.PI);
        ctx.stroke();
    });

    // Highlight the spot for the selected result row.
    if (highlightedSpot) {
        ctx.save();
        ctx.strokeStyle = "#2f6fed";
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(
            highlightedSpot.x / window.scaleFactor,
            highlightedSpot.y / window.scaleFactor,
            15,
            0,
            2 * Math.PI
        );
        ctx.stroke();
        ctx.restore();
    }

    // Optional plate region (dashed): in-progress drag or a stored region.
    const sf = window.scaleFactor || 1;
    let rect = null;
    if (draggingRegion && regionStart && regionCurrent) {
        rect = [
            Math.min(regionStart.x, regionCurrent.x),
            Math.min(regionStart.y, regionCurrent.y),
            Math.abs(regionCurrent.x - regionStart.x),
            Math.abs(regionCurrent.y - regionStart.y),
        ];
    } else if (plateRegion) {
        rect = [plateRegion.x / sf, plateRegion.y / sf, plateRegion.width / sf, plateRegion.height / sf];
    }
    if (rect) {
        ctx.save();
        ctx.strokeStyle = "#2f6f4f";
        ctx.setLineDash([6, 4]);
        ctx.strokeRect(rect[0], rect[1], rect[2], rect[3]);
        ctx.restore();
    }

    refreshUI();
}

// --- Annotation tools -----------------------------------------------------
function setActiveButton(button) {
    document.querySelectorAll(".lab-btn").forEach((b) => b.classList.remove("active"));
    if (button) button.classList.add("active");
}

function disableOtherActions() {
    addingSpots = false;
    selectingRegion = false;
    canvas.removeEventListener("click", drawSpot);
    canvas.removeEventListener("click", drawLine);
}

function selectPlateArea(event) {
    disableOtherActions();
    selectingRegion = true;
    updateAnnotatedHeading("editing");
    if (event && event.currentTarget) setActiveButton(event.currentTarget);
    notify("Drag a box around the plate, then Auto-detect. Click once to clear.", "info");
}

// Suggest Min (baseline, bottom edge) and Max (solvent front, top edge) lines
// from the plate region. Only fills in a line the user hasn't already placed;
// the suggestions are ordinary lines, so they can be dragged or deleted.
function suggestReferenceLines() {
    if (!plateRegion) return;
    let added = false;
    if (!lines.some((l) => l.type === "max")) {
        lines.push({ id: Date.now() + lineSeq++, type: "max", y: plateRegion.y, text: "max" });
        added = true;
    }
    if (!lines.some((l) => l.type === "min")) {
        const bottom = plateRegion.y + plateRegion.height;
        lines.push({ id: Date.now() + lineSeq++, type: "min", y: bottom, text: "min" });
        added = true;
    }
    if (added) {
        redrawCanvas();
        notify("Suggested Min/Max lines — drag to adjust or delete.", "info");
    }
}

function startLine(type, horizontal, event) {
    disableOtherActions();
    lineType = type;
    isHorizontal = horizontal;
    updateAnnotatedHeading("editing");
    canvas.addEventListener("click", drawLine);
    if (event && event.currentTarget) setActiveButton(event.currentTarget);
}

function addMinLine(event) { startLine("min", true, event); }
function addMaxLine(event) { startLine("max", true, event); }
function addHorizontalLine(event) { startLine("horizontal", true, event); }
function addVerticalLine(event) { startLine("vertical", false, event); }

function drawLine(event) {
    const p = pointerCanvas(event);
    const x = p.x;
    const y = p.y;

    if (isHorizontal) {
        if (lineType === "min" || lineType === "max") {
            if (lines.some((l) => l.type === lineType)) {
                notify('A "' + lineType + '" line already exists.', "warning");
                canvas.removeEventListener("click", drawLine);
                return;
            }
        }
        currentLine = {
            id: Date.now(),
            type: lineType,
            y: y * window.scaleFactor,
            text: lineType === "min" ? "min" : lineType === "max" ? "max" : "",
        };
        lines.push(currentLine);
        if (lineType !== "min" && lineType !== "max") {
            showPopup(event.pageX, event.pageY);
        }
    } else {
        currentLine = { id: Date.now(), type: lineType, x: x * window.scaleFactor, text: "" };
        lines.push(currentLine);
        showPopup(event.pageX, event.pageY);
    }
    canvas.removeEventListener("click", drawLine);
    redrawCanvas();
}

function showPopup(x, y) {
    const popup = document.getElementById("line-popup");
    const input = document.getElementById("line-text");
    popup.style.left = x + "px";
    popup.style.top = y + "px";
    popup.style.display = "block";
    input.value = "";
    setTimeout(() => input.focus(), 100);
}

function hidePopup() {
    const popup = document.getElementById("line-popup");
    if (popup) popup.style.display = "none";
    const input = document.getElementById("line-text");
    if (input) input.value = "";
    currentLine = null;
}

function submitAnnotation() {
    const text = document.getElementById("line-text").value.trim();
    if (currentLine && text !== "") currentLine.text = text;
    hidePopup();
    redrawCanvas();
}

function cancelAnnotation() {
    hidePopup();
}

function addDeleteIcon(line, x, y) {
    const icon = document.createElement("span");
    icon.className = "delete-icon";
    icon.innerHTML = "&times;";
    icon.style.left = x + "px";
    icon.style.top = y + "px";
    icon.onclick = () => deleteLine(line.id);
    document.getElementById("canvas-container").appendChild(icon);
    line.deleteIcon = icon;
}

function deleteLine(id) {
    const index = lines.findIndex((l) => l.id === id);
    if (index === -1) return;
    if (lines[index].deleteIcon) lines[index].deleteIcon.remove();
    // If the annotation popup is open for this line, close it too.
    if (currentLine && currentLine.id === id) hidePopup();
    lines.splice(index, 1);
    redrawCanvas();
}

function deleteSpot(id) {
    spots = spots.filter((s) => s.id !== id);
    redrawCanvas();
}

function addSpots(event) {
    disableOtherActions();
    addingSpots = true;
    updateAnnotatedHeading("editing");
    canvas.addEventListener("click", drawSpot);
    if (event && event.currentTarget) setActiveButton(event.currentTarget);
}

function addSpot(origX, origY) {
    const duplicate = spots.some(
        (s) => Math.abs(s.x - origX) < 12 * window.scaleFactor &&
               Math.abs(s.y - origY) < 12 * window.scaleFactor
    );
    if (duplicate) return false;
    spots.push({ id: Date.now() + spotSeq++, x: origX, y: origY });
    return true;
}

function drawSpot(event) {
    if (!addingSpots || isDragging || justDragged) return;
    const p = pointerCanvas(event);
    if (!addSpot(p.x * window.scaleFactor, p.y * window.scaleFactor)) {
        notify("Spot already exists here.", "warning");
        return;
    }
    redrawCanvas();
}

// Map a pointer event to canvas *bitmap* coordinates. Using the rendered rect
// (not event.offsetX) keeps clicks aligned with the image even when the canvas
// is CSS-scaled by max-width — the cause of the fresh-load misalignment.
function pointerCanvas(e) {
    const rect = canvas.getBoundingClientRect();
    const sx = rect.width ? canvas.width / rect.width : 1;
    const sy = rect.height ? canvas.height / rect.height : 1;
    return { x: (e.clientX - rect.left) * sx, y: (e.clientY - rect.top) * sy };
}

// --- Mouse interaction (drag / double-click delete) -----------------------
function handleMouseDown(e) {
    if (e.target.classList.contains("delete-icon")) return;
    const p = pointerCanvas(e);
    if (selectingRegion) {
        draggingRegion = true;
        regionStart = { x: p.x, y: p.y };
        regionCurrent = { x: p.x, y: p.y };
        return;
    }
    const x = p.x;
    const y = p.y;

    for (const spot of spots) {
        const dx = x - spot.x / window.scaleFactor;
        const dy = y - spot.y / window.scaleFactor;
        if (Math.sqrt(dx * dx + dy * dy) < 12) {
            draggingSpot = spot;
            isDragging = true;
            dragOffsetX = dx;
            dragOffsetY = dy;
            canvas.style.cursor = "grabbing";
            return;
        }
    }
    for (const line of lines) {
        if (isHorizontalLine(line)) {
            if (Math.abs(y - line.y / window.scaleFactor) < 6) {
                draggingLine = line;
                isDragging = true;
                lineDragOffsetY = y - line.y / window.scaleFactor;
                canvas.style.cursor = "ns-resize";
                return;
            }
        } else if (Math.abs(x - line.x / window.scaleFactor) < 6) {
            draggingLine = line;
            isDragging = true;
            lineDragOffsetX = x - line.x / window.scaleFactor;
            canvas.style.cursor = "ew-resize";
            return;
        }
    }
}

function handleMouseMove(e) {
    const p = pointerCanvas(e);
    if (draggingRegion) {
        regionCurrent = { x: p.x, y: p.y };
        redrawCanvas();
        return;
    }
    if (!isDragging) {
        let over = false;
        for (const spot of spots) {
            const dx = p.x - spot.x / window.scaleFactor;
            const dy = p.y - spot.y / window.scaleFactor;
            if (Math.sqrt(dx * dx + dy * dy) < 12) { over = true; break; }
        }
        canvas.style.cursor = over ? "pointer" : "default";
        return;
    }
    if (draggingSpot) {
        draggingSpot.x = (p.x - dragOffsetX) * window.scaleFactor;
        draggingSpot.y = (p.y - dragOffsetY) * window.scaleFactor;
    } else if (draggingLine) {
        if (isHorizontalLine(draggingLine)) {
            draggingLine.y = (p.y - lineDragOffsetY) * window.scaleFactor;
        } else {
            draggingLine.x = (p.x - lineDragOffsetX) * window.scaleFactor;
        }
    }
    redrawCanvas();
}

function handleMouseUp() {
    if (draggingRegion) {
        draggingRegion = false;
        selectingRegion = false;
        setActiveButton(null);
        const sf = window.scaleFactor || 1;
        const x0 = Math.min(regionStart.x, regionCurrent.x);
        const y0 = Math.min(regionStart.y, regionCurrent.y);
        const w = Math.abs(regionCurrent.x - regionStart.x);
        const h = Math.abs(regionCurrent.y - regionStart.y);
        regionStart = regionCurrent = null;
        if (w < 5 || h < 5) {
            plateRegion = null; // a click (no real drag) clears the region
            notify("Plate area cleared.", "info");
        } else {
            plateRegion = {
                x: Math.round(x0 * sf),
                y: Math.round(y0 * sf),
                width: Math.round(w * sf),
                height: Math.round(h * sf),
            };
            notify("Plate area set — Auto-detect will use it.", "success");
            suggestReferenceLines();
        }
        redrawCanvas();
        return;
    }
    if (!isDragging) return;
    isDragging = false;
    draggingSpot = null;
    draggingLine = null;
    canvas.style.cursor = "default";
    justDragged = true;
    setTimeout(() => { justDragged = false; }, 200);
}

function handleDoubleClick(e) {
    const p = pointerCanvas(e);
    for (const spot of spots) {
        const dx = p.x - spot.x / window.scaleFactor;
        const dy = p.y - spot.y / window.scaleFactor;
        if (Math.sqrt(dx * dx + dy * dy) < 12) {
            deleteSpot(spot.id);
            notify("Spot deleted.", "info");
            return;
        }
    }
}

// --- Image payload (shared by calculate + auto-detect) --------------------
function getImagePayload() {
    let payload;
    if (isLocalImage) {
        payload = {
            imageSrc: window.currentImageFileName || "local_upload.png",
            isLocalImage: true,
            imageData: image.src,
        };
    } else {
        try {
            const parsed = new URL(image.src);
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
    payload.rotation = rotation; // backend re-orients to match the displayed image
    return payload;
}

function hasImage() {
    return image && image.src && (image.complete ? image.naturalWidth > 0 : true);
}

// --- RF calculation -------------------------------------------------------
function calculateRF() {
    const minLine = lines.find((l) => l.type === "min");
    const maxLine = lines.find((l) => l.type === "max");
    if (!minLine || !maxLine) {
        notify('Please add "min" and "max" lines before calculating RF.', "warning");
        return;
    }
    if (spots.length === 0) {
        notify("Please add spots before calculating RF.", "warning");
        return;
    }

    const data = Object.assign({ lines: lines, spots: spots }, getImagePayload());
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

// --- Assisted spot detection ----------------------------------------------
function autoDetectSpots() {
    if (!hasImage()) {
        notify("Load an image first.", "warning");
        return;
    }
    showOverlay("Detecting spots…");
    const payload = getImagePayload();
    if (plateRegion) payload.region = plateRegion;
    const ringToggle = document.getElementById("ring-center");
    if (ringToggle) payload.ringCenter = ringToggle.checked;
    fetch("/detect-spots/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
        body: JSON.stringify(payload),
    })
        .then((response) => {
            if (!response.ok) throw new Error("HTTP " + response.status);
            return response.json();
        })
        .then((result) => {
            hideOverlay();
            let added = 0;
            (result.spots || []).forEach((s) => {
                if (addSpot(s.x, s.y)) added++;
            });
            redrawCanvas();
            updateAnnotatedHeading("editing");
            if (added > 0) {
                notify("Detected " + added + " spot(s). Review and adjust as needed.", "success");
            } else {
                notify("No new spots detected — add them manually.", "info");
            }
        })
        .catch((error) => {
            hideOverlay();
            console.error("Error detecting spots:", error);
            notify("Spot detection failed.", "error");
        });
}

function populateTable(rfValues) {
    const tbody = document.getElementById("values-table").getElementsByTagName("tbody")[0];
    tbody.innerHTML = "";
    highlightedSpot = null;
    rfValues.forEach((value, index) => {
        const row = tbody.insertRow();
        if (typeof value.x === "number" && typeof value.y === "number") {
            row.dataset.x = value.x;
            row.dataset.y = value.y;
            row.classList.add("clickable");
            row.addEventListener("click", () => highlightSpotFromRow(row));
        }
        row.insertCell(0).textContent = value.solvent || "Lane " + (index + 1);
        row.insertCell(1).textContent = value.compound || "Spot " + (index + 1);
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
    highlightedSpot = { x: Number(row.dataset.x), y: Number(row.dataset.y) };
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
        spots = spots.filter((s) => Math.abs(s.x - rx) > 1 || Math.abs(s.y - ry) > 1);
        if (highlightedSpot && Math.abs(highlightedSpot.x - rx) < 2 && Math.abs(highlightedSpot.y - ry) < 2) {
            highlightedSpot = null;
        }
    }
    row.remove();
    redrawCanvas();
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

function saveCSV() {
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

function downloadAnnotatedImage() {
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

function saveImageToServer() {
    if (isLocalImage) {
        downloadAnnotatedImage();
        return;
    }
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

function saveCSVToServer() {
    const csv = buildCsvContent();
    if (!csv) {
        notify("No CSV data to save.", "warning");
        return;
    }
    postToEln(new Blob([csv], { type: "text/csv;charset=utf-8;" }), timestampedName("csv"));
}

// --- View state -----------------------------------------------------------
function updateAnnotatedHeading(state) {
    const heading = document.getElementById("annotated-heading");
    const annotated = document.getElementById("annotated-image");
    const placeholder = document.getElementById("result-placeholder");
    if (!heading) return;
    if (state === "annotated") {
        heading.textContent = "Annotated image";
        annotated.style.display = "block";
        if (placeholder) placeholder.style.display = "none";
    } else {
        heading.textContent = "Result";
        annotated.style.display = "none";
        if (placeholder) placeholder.style.display = "";
    }
}

// --- Redesigned UI behaviour ----------------------------------------------
function toggleImageSource() {
    const body = document.getElementById("source-body");
    const summary = document.getElementById("source-summary");
    const collapsed = body.classList.toggle("hidden");
    summary.setAttribute("aria-expanded", String(!collapsed));
}

function collapseImageSource(name) {
    document.getElementById("source-body").classList.add("hidden");
    const summary = document.getElementById("source-summary");
    summary.classList.add("shown");
    summary.setAttribute("aria-expanded", "false");
    document.getElementById("source-name").textContent = name;
}

function toggleHelp() {
    document.getElementById("help-drawer").classList.toggle("open");
}

function updateWorkAreaLayout() {
    const area = document.getElementById("work-area");
    if (!area || !image) return;
    // Use the displayed (rotation-adjusted) dimensions.
    const swap = rotation === 90 || rotation === 270;
    const w = swap ? image.naturalHeight : image.naturalWidth;
    const h = swap ? image.naturalWidth : image.naturalHeight;
    // Portrait (tall) -> side-by-side columns; landscape (wide) -> stacked rows.
    const landscape = w > h;
    area.classList.toggle("layout-rows", landscape);
    area.classList.toggle("layout-columns", !landscape);
}

function updateHint() {
    const hint = document.getElementById("hint");
    if (!hint) return;
    let msg = "";
    if (!hasImage()) {
        msg = "";
    } else if (!plateRegion) {
        msg = "Tip: click Plate Area and drag a box around the plate — this makes Auto-detect far more reliable.";
    } else if (spots.length === 0) {
        msg = "Now click Auto-detect to find spots automatically (or Add Spots to place them yourself).";
    } else if (!lines.some((l) => l.type === "min") || !lines.some((l) => l.type === "max")) {
        msg = "Add Min and Max reference lines, then Calculate RF.";
    } else {
        msg = "Ready — click Calculate RF.";
    }
    hint.textContent = msg;
    hint.classList.toggle("show", msg !== "");
}

function updateCalculateEnabled() {
    const btn = document.getElementById("calculate-btn");
    if (!btn) return;
    const ready =
        hasImage() &&
        lines.some((l) => l.type === "min") &&
        lines.some((l) => l.type === "max") &&
        spots.length > 0;
    btn.disabled = !ready;
}

// Rotation is only allowed on a clean image (no annotations to invalidate),
// so the reference-line / RF axis never has to change.
function hasAnnotations() {
    return spots.length > 0 || lines.length > 0 || !!plateRegion;
}

function updateRotateEnabled() {
    const locked = hasAnnotations();
    document
        .querySelectorAll('[data-action="rotateCW"], [data-action="rotateCCW"]')
        .forEach((btn) => {
            btn.disabled = locked || !hasImage();
        });
}

function rotateImage(deg) {
    if (!hasImage() || hasAnnotations()) return;
    rotation = (rotation + deg + 360) % 360;
    fitAndDraw(image);
    refreshUI();
}

function rotateCW() {
    rotateImage(90);
}

function rotateCCW() {
    rotateImage(-90);
}

// Refresh contextual UI after any annotation change.
function refreshUI() {
    updateHint();
    updateCalculateEnabled();
    updateRotateEnabled();
}

function resetCanvas() {
    lines = [];
    spots = [];
    plateRegion = null;
    selectingRegion = false;
    highlightedSpot = null;
    document.querySelectorAll(".delete-icon").forEach((el) => el.remove());
    if (image && image.complete) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    }
    const tbody = document.querySelector("#values-table tbody");
    if (tbody) tbody.innerHTML = "";
    updateAnnotatedHeading("editing");
    setActiveButton(null);
    refreshUI();
    notify("Canvas reset.", "info");
}

// --- Image sources --------------------------------------------------------
function selectImageSource(source) {
    isLocalImage = source === "local";

    const eln = document.getElementById("eln-source-btn");
    const local = document.getElementById("local-source-btn");
    eln.classList.toggle("active", source === "eln");
    eln.classList.toggle("btn-lab-primary", source === "eln");
    eln.classList.toggle("btn-lab-outline", source !== "eln");
    local.classList.toggle("active", source === "local");
    local.classList.toggle("btn-lab-primary", source === "local");
    local.classList.toggle("btn-lab-outline", source !== "local");

    document.getElementById("local-upload-section").style.display =
        source === "local" ? "block" : "none";

    if (source === "eln") loadELNImage();
}

function handleLocalImageUpload(event) {
    const file = event.target.files[0];
    if (file) handleLocalImageFile(file);
}

function handleLocalImageFile(file) {
    if (!file.type.startsWith("image/")) {
        notify("Please select a valid image file.", "error");
        return;
    }
    if (file.size > 10 * 1024 * 1024) {
        notify("File size should be less than 10MB.", "error");
        return;
    }
    const reader = new FileReader();
    reader.onload = (e) => loadLocalImage(e.target.result, file.name);
    reader.readAsDataURL(file);
}

function loadLocalImage(dataURL, fileName) {
    rotation = 0;
    image = new Image();
    image.onload = function () {
        fitAndDraw(image);
        window.currentImageFileName = fileName;
        isLocalImage = true;
        lines = [];
        spots = [];
        plateRegion = null;
        document.querySelectorAll(".delete-icon").forEach((el) => el.remove());
        const tbody = document.querySelector("#values-table tbody");
        if (tbody) tbody.innerHTML = "";
        notify('Image "' + fileName + '" loaded.', "success");
        updateAnnotatedHeading("editing");
        collapseImageSource(fileName);
        refreshUI();
    };
    image.onerror = () => notify("Failed to load image. Please try again.", "error");
    image.src = dataURL;
}

function loadELNImage() {
    if (!elnUrl) return;
    fetch("/fetch-image/?url=" + encodeURIComponent(elnUrl))
        .then((response) => response.json())
        .then((data) => {
            if (!data.imagePath) {
                notify("ELN image not found.", "error");
                return;
            }
            fetch("/serve-image/?file_path=" + encodeURIComponent(data.imagePath)).then((response) => {
                if (response.ok) {
                    isLocalImage = false;
                    rotation = 0;
                    image = new Image();
                    image.onload = () => {
                        fitAndDraw(image);
                        refreshUI();
                    };
                    image.src = "/serve-image/?file_path=" + encodeURIComponent(data.imagePath);
                    updateAnnotatedHeading("editing");
                    collapseImageSource("ELN image");
                } else {
                    notify("Error loading ELN image.", "error");
                }
            });
        })
        .catch((error) => {
            console.error("Error fetching ELN image:", error);
            notify("Error loading ELN image.", "error");
        });
}

// --- Action dispatch (data-action -> handler; no inline JS, CSP-friendly) --
function triggerFileInput() {
    document.getElementById("local-image-input").click();
}

// Maps the `data-action` attribute on a control to its handler. Handlers that
// need the originating button receive the click event; those that take a value
// receive the element's `data-arg`.
const ACTIONS = {
    toggleTheme,
    toggleImageSource,
    toggleHelp,
    selectImageSource,
    triggerFileInput,
    addSpots,
    autoDetectSpots,
    selectPlateArea,
    rotateCW,
    rotateCCW,
    addMinLine,
    addMaxLine,
    addHorizontalLine,
    addVerticalLine,
    calculateRF,
    resetCanvas,
    updateAnnotatedHeading,
    saveCSV,
    saveImageToServer,
    saveCSVToServer,
    submitAnnotation,
    cancelAnnotation,
};

function handleAction(event) {
    const el = event.currentTarget;
    const fn = ACTIONS[el.dataset.action];
    if (!fn) return;
    if (el.dataset.arg !== undefined) {
        fn(el.dataset.arg);
    } else {
        fn(event);
    }
}

// --- Init -----------------------------------------------------------------
function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

function init() {
    canvas = document.getElementById("image-canvas");
    ctx = canvas.getContext("2d");

    // Bind all declarative controls (replaces inline onclick handlers).
    document.querySelectorAll("[data-action]").forEach((el) => {
        el.addEventListener("click", handleAction);
    });

    initTheme();

    // Persist the "center on ring marks" detection preference.
    const ringToggle = document.getElementById("ring-center");
    if (ringToggle) {
        try {
            ringToggle.checked = localStorage.getItem("tlc-ring-center") === "1";
        } catch (err) {
            /* localStorage unavailable */
        }
        ringToggle.addEventListener("change", () => {
            try {
                localStorage.setItem("tlc-ring-center", ringToggle.checked ? "1" : "0");
            } catch (err) {
                /* ignore */
            }
        });
    }

    refreshUI();

    const fileInput = document.getElementById("local-image-input");
    if (fileInput) fileInput.addEventListener("change", handleLocalImageUpload);

    const uploadArea = document.querySelector(".upload-area");
    if (uploadArea) {
        ["dragenter", "dragover", "dragleave", "drop"].forEach((evt) =>
            uploadArea.addEventListener(evt, preventDefaults, false)
        );
        ["dragenter", "dragover"].forEach((evt) =>
            uploadArea.addEventListener(evt, () => uploadArea.classList.add("dragover"))
        );
        ["dragleave", "drop"].forEach((evt) =>
            uploadArea.addEventListener(evt, () => uploadArea.classList.remove("dragover"))
        );
        uploadArea.addEventListener("drop", (e) => {
            if (e.dataTransfer.files.length > 0) handleLocalImageFile(e.dataTransfer.files[0]);
        });
    }

    canvas.addEventListener("mousedown", handleMouseDown);
    canvas.addEventListener("mousemove", handleMouseMove);
    canvas.addEventListener("mouseup", handleMouseUp);
    canvas.addEventListener("dblclick", handleDoubleClick);

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            disableOtherActions();
            hidePopup();
        }
    });

    // Default to ELN when a url param is present, otherwise local upload.
    selectImageSource(pageUrl.searchParams.get("url") ? "eln" : "local");
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}

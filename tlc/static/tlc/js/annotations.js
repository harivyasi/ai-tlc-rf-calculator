/*
 * Annotation tools: reference/label lines, spots, the plate region, the line
 * label popup, and the active-tool button highlight. Click handlers for placing
 * lines and spots are attached to / detached from the canvas on demand.
 */

"use strict";

import { state } from "./state.js";
import { notify } from "./ui-kit.js";
import { pointInRegion } from "./geometry.js";
import { updateAnnotatedHeading } from "./ui.js";
import { pointerCanvas, redrawCanvas } from "./canvas.js";

// --- Line-type predicate (was repeated inline throughout) -----------------
export function isHorizontalLine(line) {
    return line.type === "horizontal" || line.type === "min" || line.type === "max";
}

export function setActiveButton(button) {
    document.querySelectorAll(".lab-btn").forEach((b) => b.classList.remove("active"));
    if (button) button.classList.add("active");
}

export function disableOtherActions() {
    state.addingSpots = false;
    state.selectingRegion = false;
    state.canvas.removeEventListener("click", drawSpot);
    state.canvas.removeEventListener("click", drawLine);
}

export function selectPlateArea(event) {
    disableOtherActions();
    state.selectingRegion = true;
    updateAnnotatedHeading("editing");
    if (event && event.currentTarget) setActiveButton(event.currentTarget);
    notify("Drag a box around the plate, then Auto-detect. Click once to clear.", "info");
}

// Note: the plate region is a detection ROI only. The Min (baseline) and Max
// (solvent front) lines are experimental features inside the plate — not its
// edges — so they are placed by the user, never derived from the plate box.

function startLine(type, horizontal, event) {
    disableOtherActions();
    state.lineType = type;
    state.isHorizontal = horizontal;
    updateAnnotatedHeading("editing");
    state.canvas.addEventListener("click", drawLine);
    if (event && event.currentTarget) setActiveButton(event.currentTarget);
}

export function addMinLine(event) { startLine("min", true, event); }
export function addMaxLine(event) { startLine("max", true, event); }
export function addHorizontalLine(event) { startLine("horizontal", true, event); }
export function addVerticalLine(event) { startLine("vertical", false, event); }

export function drawLine(event) {
    const p = pointerCanvas(event);
    const x = p.x;
    const y = p.y;

    // With a plate area set, a line must fall inside it (horizontal on y,
    // vertical on x). Block the click but keep the tool armed for a retry.
    const r = state.plateRegion;
    if (r) {
        const outside = state.isHorizontal
            ? y * window.scaleFactor < r.y || y * window.scaleFactor > r.y + r.height
            : x * window.scaleFactor < r.x || x * window.scaleFactor > r.x + r.width;
        if (outside) {
            notify("Place it inside the plate area.", "warning");
            return;
        }
    }

    if (state.isHorizontal) {
        if (state.lineType === "min" || state.lineType === "max") {
            if (state.lines.some((l) => l.type === state.lineType)) {
                notify('A "' + state.lineType + '" line already exists.', "warning");
                state.canvas.removeEventListener("click", drawLine);
                return;
            }
        }
        state.currentLine = {
            id: Date.now(),
            type: state.lineType,
            y: y * window.scaleFactor,
            text: state.lineType === "min" ? "min" : state.lineType === "max" ? "max" : "",
        };
        state.lines.push(state.currentLine);
        if (state.lineType !== "min" && state.lineType !== "max") {
            showPopup(event.pageX, event.pageY);
        }
    } else {
        state.currentLine = {
            id: Date.now(),
            type: state.lineType,
            x: x * window.scaleFactor,
            text: "",
        };
        state.lines.push(state.currentLine);
        showPopup(event.pageX, event.pageY);
    }
    state.canvas.removeEventListener("click", drawLine);
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

export function hidePopup() {
    const popup = document.getElementById("line-popup");
    if (popup) popup.style.display = "none";
    const input = document.getElementById("line-text");
    if (input) input.value = "";
    state.currentLine = null;
}

export function submitAnnotation() {
    const text = document.getElementById("line-text").value.trim();
    if (state.currentLine && text !== "") state.currentLine.text = text;
    hidePopup();
    redrawCanvas();
}

export function cancelAnnotation() {
    hidePopup();
}

export function deleteLine(id) {
    const index = state.lines.findIndex((l) => l.id === id);
    if (index === -1) return;
    if (state.lines[index].deleteIcon) state.lines[index].deleteIcon.remove();
    // If the annotation popup is open for this line, close it too.
    if (state.currentLine && state.currentLine.id === id) hidePopup();
    state.lines.splice(index, 1);
    redrawCanvas();
}

export function deleteSpot(id) {
    state.spots = state.spots.filter((s) => s.id !== id);
    redrawCanvas();
}

export function addSpots(event) {
    disableOtherActions();
    state.addingSpots = true;
    updateAnnotatedHeading("editing");
    state.canvas.addEventListener("click", drawSpot);
    if (event && event.currentTarget) setActiveButton(event.currentTarget);
}

export function addSpot(origX, origY, auto = false) {
    const duplicate = state.spots.some(
        (s) => Math.abs(s.x - origX) < 12 * window.scaleFactor &&
               Math.abs(s.y - origY) < 12 * window.scaleFactor
    );
    if (duplicate) return false;
    // `auto` marks spots placed by Auto-detect, so re-running detection (e.g.
    // when the "ring marks" preference changes) can replace only those.
    state.spots.push({ id: Date.now() + state.spotSeq++, x: origX, y: origY, auto });
    return true;
}

export function drawSpot(event) {
    if (!state.addingSpots || state.isDragging || state.justDragged) return;
    const p = pointerCanvas(event);
    const ox = p.x * window.scaleFactor;
    const oy = p.y * window.scaleFactor;
    if (state.plateRegion && !pointInRegion(ox, oy, state.plateRegion)) {
        notify("Place spots inside the plate area.", "warning");
        return;
    }
    if (!addSpot(ox, oy)) {
        notify("Spot already exists here.", "warning");
        return;
    }
    redrawCanvas();
}

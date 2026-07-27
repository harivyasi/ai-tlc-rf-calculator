/*
 * Canvas pointer interaction: dragging spots and lines, drawing the plate
 * region box, and deleting a spot. Uses Pointer Events so mouse, touch, and pen
 * all work through one code path; the canvas sets `touch-action: none` so touch
 * drags aren't stolen by the browser's scroll/zoom gestures.
 */

"use strict";

import { state } from "./state.js";
import { notify } from "./ui-kit.js";
import { applyCrop, pointerCanvas, redrawCanvas } from "./canvas.js";
import { deleteSpot, isHorizontalLine, setActiveButton, suggestReferenceLines } from "./annotations.js";

// Tap/drag tracking (module-local; not shared state).
let downX = 0;
let downY = 0;
let downActive = false; // a pointer is currently pressed
let downMoved = false; // it has moved far enough to count as a drag
let lastTap = null; // {t, x, y} of the previous touch tap (for double-tap delete)
const TAP_MOVE_PX = 8; // movement below this is a tap, not a drag
const DOUBLE_TAP_MS = 300;

function captureCanvas(e) {
    try {
        state.canvas.setPointerCapture(e.pointerId);
    } catch (err) {
        /* synthetic/untrusted events have no real pointerId — drag still works */
    }
}

function releaseCanvas(e) {
    try {
        if (state.canvas.hasPointerCapture(e.pointerId)) {
            state.canvas.releasePointerCapture(e.pointerId);
        }
    } catch (err) {
        /* ignore */
    }
}

// Delete the spot under a display-coordinate point, if any. Returns true if one
// was removed. Shared by mouse double-click and touch double-tap.
function deleteSpotAt(px, py) {
    for (const spot of state.spots) {
        const dx = px - spot.x / window.scaleFactor;
        const dy = py - spot.y / window.scaleFactor;
        if (Math.sqrt(dx * dx + dy * dy) < 12) {
            deleteSpot(spot.id);
            notify("Spot deleted.", "info");
            return true;
        }
    }
    return false;
}

export function handlePointerDown(e) {
    if (e.target.classList.contains("delete-icon")) return;
    const p = pointerCanvas(e);
    downX = p.x;
    downY = p.y;
    downActive = true;
    downMoved = false;

    if (state.selectingRegion) {
        state.draggingRegion = true;
        state.regionStart = { x: p.x, y: p.y };
        state.regionCurrent = { x: p.x, y: p.y };
        captureCanvas(e);
        return;
    }
    const x = p.x;
    const y = p.y;

    for (const spot of state.spots) {
        const dx = x - spot.x / window.scaleFactor;
        const dy = y - spot.y / window.scaleFactor;
        if (Math.sqrt(dx * dx + dy * dy) < 12) {
            state.draggingSpot = spot;
            state.isDragging = true;
            state.dragOffsetX = dx;
            state.dragOffsetY = dy;
            state.canvas.style.cursor = "grabbing";
            captureCanvas(e);
            return;
        }
    }
    for (const line of state.lines) {
        if (isHorizontalLine(line)) {
            if (Math.abs(y - line.y / window.scaleFactor) < 6) {
                state.draggingLine = line;
                state.isDragging = true;
                state.lineDragOffsetY = y - line.y / window.scaleFactor;
                state.canvas.style.cursor = "ns-resize";
                captureCanvas(e);
                return;
            }
        } else if (Math.abs(x - line.x / window.scaleFactor) < 6) {
            state.draggingLine = line;
            state.isDragging = true;
            state.lineDragOffsetX = x - line.x / window.scaleFactor;
            state.canvas.style.cursor = "ew-resize";
            captureCanvas(e);
            return;
        }
    }
}

export function handlePointerMove(e) {
    const p = pointerCanvas(e);
    if (downActive && (Math.abs(p.x - downX) > TAP_MOVE_PX || Math.abs(p.y - downY) > TAP_MOVE_PX)) {
        downMoved = true;
    }
    if (state.draggingRegion) {
        state.regionCurrent = { x: p.x, y: p.y };
        redrawCanvas();
        return;
    }
    if (!state.isDragging) {
        // Hover feedback only makes sense for a mouse (touch/pen have no hover).
        if (e.pointerType !== "mouse") return;
        let over = false;
        for (const spot of state.spots) {
            const dx = p.x - spot.x / window.scaleFactor;
            const dy = p.y - spot.y / window.scaleFactor;
            if (Math.sqrt(dx * dx + dy * dy) < 12) { over = true; break; }
        }
        state.canvas.style.cursor = over ? "pointer" : "default";
        return;
    }
    if (state.draggingSpot) {
        state.draggingSpot.x = (p.x - state.dragOffsetX) * window.scaleFactor;
        state.draggingSpot.y = (p.y - state.dragOffsetY) * window.scaleFactor;
    } else if (state.draggingLine) {
        if (isHorizontalLine(state.draggingLine)) {
            state.draggingLine.y = (p.y - state.lineDragOffsetY) * window.scaleFactor;
        } else {
            state.draggingLine.x = (p.x - state.lineDragOffsetX) * window.scaleFactor;
        }
    }
    redrawCanvas();
}

export function handlePointerUp(e) {
    releaseCanvas(e);
    const isTouch = e.pointerType !== "mouse";
    const wasDrag = downMoved;
    downActive = false;

    if (state.draggingRegion) {
        state.draggingRegion = false;
        state.selectingRegion = false;
        setActiveButton(null);
        const sf = window.scaleFactor || 1;
        const x0 = Math.min(state.regionStart.x, state.regionCurrent.x);
        const y0 = Math.min(state.regionStart.y, state.regionCurrent.y);
        const w = Math.abs(state.regionCurrent.x - state.regionStart.x);
        const h = Math.abs(state.regionCurrent.y - state.regionStart.y);
        state.regionStart = null;
        state.regionCurrent = null;
        if (w < 5 || h < 5) {
            state.plateRegion = null; // a click (no real drag) clears the region
            notify("Plate area cleared.", "info");
        } else {
            state.plateRegion = {
                x: Math.round(x0 * sf),
                y: Math.round(y0 * sf),
                width: Math.round(w * sf),
                height: Math.round(h * sf),
            };
            notify("Plate area set — Auto-detect will use it.", "success");
            suggestReferenceLines();
        }
        applyCrop(); // re-fit the crop view to the new/cleared region (redraws)
        return;
    }

    if (state.isDragging) {
        state.isDragging = false;
        state.draggingSpot = null;
        state.draggingLine = null;
        state.canvas.style.cursor = "default";
        justDraggedGuard();
    }

    // Touch double-tap to delete a spot (mouse keeps its native dblclick).
    if (isTouch && !wasDrag) {
        const now = Date.now();
        if (
            lastTap &&
            now - lastTap.t < DOUBLE_TAP_MS &&
            Math.abs(downX - lastTap.x) < 12 &&
            Math.abs(downY - lastTap.y) < 12
        ) {
            lastTap = null;
            if (deleteSpotAt(downX, downY)) {
                // Suppress the tap's trailing `click` so drawSpot doesn't
                // immediately re-add a spot at the just-cleared location.
                justDraggedGuard();
            }
        } else {
            lastTap = { t: now, x: downX, y: downY };
        }
    }
}

export function handlePointerCancel(e) {
    releaseCanvas(e);
    downActive = false;
    if (state.draggingRegion) {
        state.draggingRegion = false;
        state.selectingRegion = false;
        state.regionStart = null;
        state.regionCurrent = null;
        setActiveButton(null);
    }
    if (state.isDragging) {
        state.isDragging = false;
        state.draggingSpot = null;
        state.draggingLine = null;
        state.canvas.style.cursor = "default";
    }
    redrawCanvas();
}

export function handleDoubleClick(e) {
    const p = pointerCanvas(e);
    deleteSpotAt(p.x, p.y);
}

// After a real drag, briefly suppress the trailing click so it doesn't place a
// new spot / line (mirrors the original mouseup behaviour).
function justDraggedGuard() {
    state.justDragged = true;
    setTimeout(() => { state.justDragged = false; }, 200);
}

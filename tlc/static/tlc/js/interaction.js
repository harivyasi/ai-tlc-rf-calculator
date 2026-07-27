/*
 * Canvas pointer interaction: dragging spots and lines, drawing the plate
 * region box, and deleting a spot. (Phase 1 keeps the original mouse handlers;
 * the pointer/touch migration lives in a follow-up commit.)
 */

"use strict";

import { state } from "./state.js";
import { notify } from "./ui-kit.js";
import { applyCrop, pointerCanvas, redrawCanvas } from "./canvas.js";
import { deleteSpot, isHorizontalLine, setActiveButton, suggestReferenceLines } from "./annotations.js";

export function handleMouseDown(e) {
    if (e.target.classList.contains("delete-icon")) return;
    const p = pointerCanvas(e);
    if (state.selectingRegion) {
        state.draggingRegion = true;
        state.regionStart = { x: p.x, y: p.y };
        state.regionCurrent = { x: p.x, y: p.y };
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
                return;
            }
        } else if (Math.abs(x - line.x / window.scaleFactor) < 6) {
            state.draggingLine = line;
            state.isDragging = true;
            state.lineDragOffsetX = x - line.x / window.scaleFactor;
            state.canvas.style.cursor = "ew-resize";
            return;
        }
    }
}

export function handleMouseMove(e) {
    const p = pointerCanvas(e);
    if (state.draggingRegion) {
        state.regionCurrent = { x: p.x, y: p.y };
        redrawCanvas();
        return;
    }
    if (!state.isDragging) {
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

export function handleMouseUp() {
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
    if (!state.isDragging) return;
    state.isDragging = false;
    state.draggingSpot = null;
    state.draggingLine = null;
    state.canvas.style.cursor = "default";
    state.justDragged = true;
    setTimeout(() => { state.justDragged = false; }, 200);
}

export function handleDoubleClick(e) {
    const p = pointerCanvas(e);
    for (const spot of state.spots) {
        const dx = p.x - spot.x / window.scaleFactor;
        const dy = p.y - spot.y / window.scaleFactor;
        if (Math.sqrt(dx * dx + dy * dy) < 12) {
            deleteSpot(spot.id);
            notify("Spot deleted.", "info");
            return;
        }
    }
}

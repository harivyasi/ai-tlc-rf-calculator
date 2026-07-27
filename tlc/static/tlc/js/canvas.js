/*
 * Canvas rendering: drawing the image at the current rotation, redrawing all
 * annotations, mapping pointer events to bitmap coordinates, the crop-to-plate
 * view (display only), and rotate/reset.
 */

"use strict";

import { state } from "./state.js";
import { notify } from "./ui-kit.js";
import {
    hasAnnotations,
    hasImage,
    refreshUI,
    updateAnnotatedHeading,
    updateWorkAreaLayout,
} from "./ui.js";
import { deleteLine, isHorizontalLine, setActiveButton } from "./annotations.js";

// Draw an image into the canvas frame applying the current clockwise rotation.
export function drawImageRotated(context, img, rot, w, h) {
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

export function fitAndDraw(img) {
    const maxHeight = 500;
    const natW0 = img.naturalWidth || img.width;
    const natH0 = img.naturalHeight || img.height;
    // For 90/270 the displayed image's width/height are swapped.
    const swap = state.rotation === 90 || state.rotation === 270;
    const naturalW = swap ? natH0 : natW0;
    const naturalH = swap ? natW0 : natH0;
    let dispW = naturalW;
    let dispH = naturalH;
    if (naturalH > maxHeight) {
        dispW = Math.round((maxHeight * naturalW) / naturalH);
        dispH = maxHeight;
    }
    state.canvas.width = dispW;
    state.canvas.height = dispH;
    drawImageRotated(state.ctx, img, state.rotation, dispW, dispH);
    window.scaleFactor = naturalH / dispH;

    const annotated = document.getElementById("annotated-image");
    annotated.style.width = dispW + "px";
    annotated.style.height = dispH + "px";

    updateWorkAreaLayout();
    document.body.dataset.imageReady = "1"; // readiness signal (used by tests)
}

export function redrawCanvas() {
    const canvas = state.canvas;
    const ctx = state.ctx;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawImageRotated(ctx, state.image, state.rotation, canvas.width, canvas.height);

    ctx.font = "16px sans-serif";
    ctx.lineWidth = 2;

    // Lines.
    ctx.strokeStyle = "red";
    ctx.fillStyle = "red";
    state.lines.forEach((line) => {
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
    const fixed = state.cropView ? 4 : 10; // margin for the line's fixed axis
    state.lines.forEach((line) => {
        if (isHorizontalLine(line)) {
            addDeleteIcon(line, fixed, toContainer(0, line.y / window.scaleFactor).y);
        } else {
            addDeleteIcon(line, toContainer(line.x / window.scaleFactor, 0).x, fixed);
        }
    });

    // Spots.
    ctx.strokeStyle = "orange";
    state.spots.forEach((spot) => {
        ctx.beginPath();
        ctx.arc(spot.x / window.scaleFactor, spot.y / window.scaleFactor, 10, 0, 2 * Math.PI);
        ctx.stroke();
    });

    // Highlight the spot for the selected result row.
    if (state.highlightedSpot) {
        ctx.save();
        ctx.strokeStyle = "#2f6fed";
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(
            state.highlightedSpot.x / window.scaleFactor,
            state.highlightedSpot.y / window.scaleFactor,
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
    if (state.draggingRegion && state.regionStart && state.regionCurrent) {
        rect = [
            Math.min(state.regionStart.x, state.regionCurrent.x),
            Math.min(state.regionStart.y, state.regionCurrent.y),
            Math.abs(state.regionCurrent.x - state.regionStart.x),
            Math.abs(state.regionCurrent.y - state.regionStart.y),
        ];
    } else if (state.plateRegion) {
        rect = [
            state.plateRegion.x / sf,
            state.plateRegion.y / sf,
            state.plateRegion.width / sf,
            state.plateRegion.height / sf,
        ];
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

export function addDeleteIcon(line, x, y) {
    const icon = document.createElement("span");
    icon.className = "delete-icon";
    icon.innerHTML = "&times;";
    icon.style.left = x + "px";
    icon.style.top = y + "px";
    icon.onclick = () => deleteLine(line.id);
    document.getElementById("canvas-container").appendChild(icon);
    line.deleteIcon = icon;
}

// Map a pointer event to canvas *bitmap* coordinates. Using the rendered rect
// (not event.offsetX) keeps clicks aligned with the image even when the canvas
// is CSS-scaled by max-width — the cause of the fresh-load misalignment.
export function pointerCanvas(e) {
    const canvas = state.canvas;
    const rect = canvas.getBoundingClientRect();
    const sx = rect.width ? canvas.width / rect.width : 1;
    const sy = rect.height ? canvas.height / rect.height : 1;
    return { x: (e.clientX - rect.left) * sx, y: (e.clientY - rect.top) * sy };
}

// --- Crop-to-plate view (display only; coordinates and saved image unchanged) --
export function cropEnabled() {
    const box = document.getElementById("crop-plate");
    return !!(box && box.checked && state.plateRegion && hasImage());
}

// Map canvas-display coordinates to #canvas-container coordinates, accounting
// for the crop zoom (delete icons live in the container, not on the canvas).
export function toContainer(dx, dy) {
    const cropView = state.cropView;
    if (!cropView) return { x: dx, y: dy };
    return { x: (dx - cropView.rcx) * cropView.k, y: (dy - cropView.rcy) * cropView.k };
}

// Apply/clear the crop by CSS-zooming the canvas inside an overflow-hidden
// container. The bitmap, annotation coordinates, and saved image are untouched;
// pointer mapping stays correct because it reads getBoundingClientRect().
export function applyCrop() {
    const canvas = state.canvas;
    const container = document.getElementById("canvas-container");
    const props = ["position", "left", "top", "width", "height", "maxWidth"];
    if (cropEnabled()) {
        const sf = window.scaleFactor || 1;
        const rcx = state.plateRegion.x / sf;
        const rcy = state.plateRegion.y / sf;
        const rcw = state.plateRegion.width / sf;
        const rch = state.plateRegion.height / sf;
        const k = rch > 0 ? Math.min(3, 500 / rch) : 1;
        state.cropView = { rcx, rcy, k };
        container.style.overflow = "hidden";
        container.style.width = Math.round(rcw * k) + "px";
        container.style.height = Math.round(rch * k) + "px";
        container.style.margin = "0 auto";
        canvas.style.position = "absolute";
        canvas.style.left = Math.round(-rcx * k) + "px";
        canvas.style.top = Math.round(-rcy * k) + "px";
        canvas.style.width = Math.round(canvas.width * k) + "px";
        canvas.style.height = Math.round(canvas.height * k) + "px";
        canvas.style.maxWidth = "none";
    } else {
        state.cropView = null;
        container.style.overflow = "";
        container.style.width = "";
        container.style.height = "";
        container.style.margin = "";
        props.forEach((p) => (canvas.style[p] = ""));
    }
    redrawCanvas();
}

export function rotateImage(deg) {
    if (!hasImage() || hasAnnotations()) return;
    state.rotation = (state.rotation + deg + 360) % 360;
    fitAndDraw(state.image);
    refreshUI();
}

export function rotateCW() {
    rotateImage(90);
}

export function rotateCCW() {
    rotateImage(-90);
}

export function resetCanvas() {
    const canvas = state.canvas;
    const ctx = state.ctx;
    state.lines = [];
    state.spots = [];
    state.plateRegion = null;
    state.selectingRegion = false;
    state.highlightedSpot = null;
    document.querySelectorAll(".delete-icon").forEach((el) => el.remove());
    if (state.image && state.image.complete) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
    }
    const tbody = document.querySelector("#values-table tbody");
    if (tbody) tbody.innerHTML = "";
    updateAnnotatedHeading("editing");
    setActiveButton(null);
    applyCrop(); // region cleared -> exit crop view (redraws)
    refreshUI();
    notify("Canvas reset.", "info");
}

/*
 * Shared mutable state for the annotation UI.
 *
 * ES modules make imported bindings read-only in the importer, so all mutable
 * state lives as properties of a single exported object: every module reads and
 * writes `state.foo` rather than re-binding a top-level `let`. `canvas`/`ctx`
 * are assigned in init() (see main.js) — never captured at import time.
 */

"use strict";

export const pageUrl = new URL(window.location.href);

const encodedUrl = pageUrl.searchParams.get("url");
// Chemotion passes one signed URL (via ?url=) that serves the source image on
// GET and accepts the annotated results on POST — the same endpoint for both.
export const elnUrl = encodedUrl ? decodeURIComponent(encodedUrl) : null;

export const state = {
    image: new Image(),
    canvas: null,
    ctx: null,

    lines: [],
    spots: [],
    spotSeq: 0,
    lineSeq: 0,
    rotation: 0, // clockwise degrees the image is displayed at (0/90/180/270)

    currentLine: null,
    isHorizontal: true,
    lineType: "",
    addingSpots: false,

    isLocalImage: false,

    // Drag state.
    draggingSpot: null,
    draggingLine: null,
    isDragging: false,
    dragOffsetX: 0,
    dragOffsetY: 0,
    lineDragOffsetX: 0,
    lineDragOffsetY: 0,
    justDragged: false,

    // Optional plate region-of-interest that constrains auto-detect.
    plateRegion: null, // {x, y, width, height} in original-image coordinates
    selectingRegion: false, // the "Plate Area" tool is armed
    draggingRegion: false, // a region drag is in progress
    regionStart: null, // {x, y} in display coordinates
    regionCurrent: null, // {x, y} in display coordinates
    highlightedSpot: null, // {x, y} of a result row highlighted on the canvas
    cropView: null, // when cropped-to-plate: {rcx, rcy, k} in canvas display px

    hasResults: false, // a calculation has produced a table + annotated image
};

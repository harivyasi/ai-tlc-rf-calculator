/*
 * TLC RF Calculator — annotation UI entry point.
 *
 * Wires the declarative `data-action` controls to their handlers, binds canvas
 * interaction, and initialises the page. The behaviour is split across sibling
 * ES modules (state, ui-kit, theme, ui, canvas, annotations, interaction,
 * results, sources); this module owns only the action dispatch table and init.
 *
 * Self-contained: no external libraries.
 */

"use strict";

import { pageUrl, state } from "./state.js";
import { preventDefaults } from "./ui-kit.js";
import { initTheme, toggleTheme } from "./theme.js";
import { refreshUI, toggleHelp, toggleImageSource, updateAnnotatedHeading } from "./ui.js";
import { applyCrop, rotateCCW, rotateCW, resetCanvas } from "./canvas.js";
import {
    addHorizontalLine,
    addMaxLine,
    addMinLine,
    addSpots,
    addVerticalLine,
    cancelAnnotation,
    disableOtherActions,
    hidePopup,
    selectPlateArea,
    submitAnnotation,
} from "./annotations.js";
import { handleDoubleClick, handleMouseDown, handleMouseMove, handleMouseUp } from "./interaction.js";
import { autoDetectSpots, calculateRF, saveCSV, saveCSVToServer, saveImageToServer } from "./results.js";
import { handleLocalImageFile, handleLocalImageUpload, selectImageSource, triggerFileInput } from "./sources.js";

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

function init() {
    state.canvas = document.getElementById("image-canvas");
    state.ctx = state.canvas.getContext("2d");

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

    // "Crop view" preference: persisted; re-applies the crop on toggle.
    const cropToggle = document.getElementById("crop-plate");
    if (cropToggle) {
        try {
            cropToggle.checked = localStorage.getItem("tlc-crop-view") === "1";
        } catch (err) {
            /* ignore */
        }
        cropToggle.addEventListener("change", () => {
            try {
                localStorage.setItem("tlc-crop-view", cropToggle.checked ? "1" : "0");
            } catch (err) {
                /* ignore */
            }
            applyCrop();
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

    state.canvas.addEventListener("mousedown", handleMouseDown);
    state.canvas.addEventListener("mousemove", handleMouseMove);
    state.canvas.addEventListener("mouseup", handleMouseUp);
    state.canvas.addEventListener("dblclick", handleDoubleClick);

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

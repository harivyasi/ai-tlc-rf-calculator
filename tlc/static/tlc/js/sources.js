/*
 * Image sources: the ELN/local source toggle, local file upload (picker and
 * drag-and-drop), and fetching an image from the ELN.
 */

"use strict";

import { elnUrl, state } from "./state.js";
import { getCookie, notify } from "./ui-kit.js";
import {
    clearResultsStale,
    collapseImageSource,
    refreshUI,
    updateAnnotatedHeading,
    updateExportButtons,
} from "./ui.js";
import { applyCrop, fitAndDraw, syncRotationControls } from "./canvas.js";
import { resetHistory } from "./history.js";

export function triggerFileInput() {
    document.getElementById("local-image-input").click();
}

export function selectImageSource(source) {
    state.isLocalImage = source === "local";

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

    updateExportButtons(); // ELN-upload buttons only for ELN-sourced plates
    if (source === "eln") loadELNImage();
}

export function handleLocalImageUpload(event) {
    const file = event.target.files[0];
    if (file) handleLocalImageFile(file);
}

export function handleLocalImageFile(file) {
    if (!file.type.startsWith("image/")) {
        notify("Please select a valid image file.", "error");
        return;
    }
    // The server's limit, passed in by the page so the two cannot disagree.
    const maxMb = Number(document.body.dataset.maxImageMb) || 25;
    if (file.size > maxMb * 1024 * 1024) {
        notify("The image must be smaller than " + maxMb + " MB.", "error");
        return;
    }
    const reader = new FileReader();
    reader.onload = (e) => loadLocalImage(e.target.result, file.name, file);
    reader.readAsDataURL(file);
}

// Store the file on the server once. Resolves to its stored name, or null if
// the upload fails -- requests then fall back to sending the image inline.
function uploadLocalImage(file) {
    const form = new FormData();
    form.append("image", file, file.name);
    return fetch("/upload-image/", {
        method: "POST",
        headers: { "X-CSRFToken": getCookie("csrftoken") },
        body: form,
    })
        .then((response) => response.json().then((body) => (response.ok ? body.imagePath : null)))
        .catch(() => null);
}

function loadLocalImage(dataURL, fileName, file) {
    // Start the upload while the image is decoded and drawn.
    state.localImageUpload = file ? uploadLocalImage(file) : Promise.resolve(null);
    state.rotation = 0;
    state.fineRotation = 0;
    syncRotationControls();
    state.image = new Image();
    state.image.onload = function () {
        fitAndDraw(state.image);
        window.currentImageFileName = fileName;
        state.isLocalImage = true;
        state.lines = [];
        state.spots = [];
        state.plateRegion = null;
        document.querySelectorAll(".delete-icon").forEach((el) => el.remove());
        const tbody = document.querySelector("#values-table tbody");
        if (tbody) tbody.innerHTML = "";
        state.hasResults = false;
        state.csvUrl = null;
        clearResultsStale();
        resetHistory(); // fresh image = undo baseline
        notify('Image "' + fileName + '" loaded.', "success");
        updateAnnotatedHeading("editing");
        collapseImageSource(fileName);
        applyCrop(); // no region yet -> ensure crop view is reset
        refreshUI();
    };
    state.image.onerror = () => notify("Failed to load image. Please try again.", "error");
    state.image.src = dataURL;
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
                    state.isLocalImage = false;
                    state.rotation = 0;
                    state.fineRotation = 0;
                    syncRotationControls();
                    state.image = new Image();
                    state.image.onload = () => {
                        fitAndDraw(state.image);
                        applyCrop();
                        refreshUI();
                    };
                    state.image.src = "/serve-image/?file_path=" + encodeURIComponent(data.imagePath);
                    state.hasResults = false;
                    state.csvUrl = null;
                    clearResultsStale();
                    resetHistory();
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

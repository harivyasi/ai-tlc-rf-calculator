/*
 * Image sources: the ELN/local source toggle, local file upload (picker and
 * drag-and-drop), and fetching an image from the ELN.
 */

"use strict";

import { elnUrl, state } from "./state.js";
import { notify } from "./ui-kit.js";
import { clearResultsStale, collapseImageSource, refreshUI, updateAnnotatedHeading } from "./ui.js";
import { applyCrop, fitAndDraw } from "./canvas.js";

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
    if (file.size > 10 * 1024 * 1024) {
        notify("File size should be less than 10MB.", "error");
        return;
    }
    const reader = new FileReader();
    reader.onload = (e) => loadLocalImage(e.target.result, file.name);
    reader.readAsDataURL(file);
}

function loadLocalImage(dataURL, fileName) {
    state.rotation = 0;
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
        clearResultsStale();
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
                    state.image = new Image();
                    state.image.onload = () => {
                        fitAndDraw(state.image);
                        applyCrop();
                        refreshUI();
                    };
                    state.image.src = "/serve-image/?file_path=" + encodeURIComponent(data.imagePath);
                    state.hasResults = false;
                    clearResultsStale();
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

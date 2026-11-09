/*
 * Small self-contained UI primitives: toasts, a busy overlay, and a couple of
 * pure helpers. Replaces the old Materialize toast / SweetAlert dependencies.
 */

"use strict";

export function notify(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = "toast toast-" + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.classList.add("toast-hide"), 3000);
    setTimeout(() => toast.remove(), 3400);
}

export function showOverlay(text) {
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

export function hideOverlay() {
    const overlay = document.getElementById("busy-overlay");
    if (overlay) overlay.style.display = "none";
}

export function getCookie(name) {
    const match = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return match ? decodeURIComponent(match.pop()) : null;
}

export function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

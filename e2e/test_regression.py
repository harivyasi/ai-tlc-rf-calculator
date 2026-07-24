"""Playwright regression tests: refactored frontend vs. the original.

Run with::

    uv run pytest e2e -v

The same browser flow is exercised against the ``new`` and ``legacy`` servers.
The core assertion is that the local-upload → annotate → calculate-RF flow
produces the identical result on both frontends (no regression). Two further
tests document the intended bug fixes in the refactored JavaScript (the removed
eager/duplicate image fetches).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

# Minimal stand-ins for the CDN libraries so the suite is hermetic (no network).
_CDN_SHIM_JS = """
window.M = window.M || {
    toast: function () {},
    Tooltip: { init: function () {} },
    Dropdown: { init: function () {} },
};
window.Swal = window.Swal || {
    fire: function () { return { then: function (cb) { if (cb) cb({}); } }; },
    showLoading: function () {},
    close: function () {},
};
"""


def _stub_cdn(page: Page) -> None:
    """Fulfil external CDN/font requests locally so tests need no network."""

    def handle(route):
        url = route.request.url
        if "sweetalert" in url or url.endswith(".js") or "materialize.min.js" in url:
            route.fulfill(status=200, content_type="text/javascript", body=_CDN_SHIM_JS)
        else:
            route.fulfill(status=200, content_type="text/css", body="")

    for pattern in (
        "**cdnjs.cloudflare.com/**",
        "**cdn.jsdelivr.net/**",
        "**fonts.googleapis.com/**",
    ):
        page.route(pattern, handle)


def _load_local_image_and_calculate(page: Page, base_url: str, image_path: str) -> str:
    """Run the full local-upload flow; return the first RF value in the table.

    With scaleFactor == 1, clicks map 1:1 to image coordinates. min at y=250,
    max at y=50, spot at y=150 gives RF = (150-250)/(50-250) = 0.5. A constant
    canvas-border offset cancels in the ratio, so the value stays 0.5.
    """
    _stub_cdn(page)
    page.goto(base_url + "/")

    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", image_path)

    # loadLocalImage() flips the heading to "Prepare Annotations" once ready.
    page.wait_for_function(
        "() => { const h = document.getElementById('annotated-heading');"
        " return h && h.textContent.trim() === 'Prepare Annotations'; }"
    )

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 50})
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})

    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    return page.locator("#values-table tbody tr").first.locator("td").nth(2).inner_text().strip()


@pytest.mark.parametrize("frontend", ["new", "legacy"])
def test_local_upload_rf_matches(page, servers, test_image_path, frontend):
    """Core regression: both frontends compute the same RF via the new backend."""
    rf = _load_local_image_and_calculate(page, servers[frontend], test_image_path)
    assert rf == "0.5", f"{frontend} frontend produced RF={rf!r}"


def test_new_frontend_makes_no_spurious_fetch_on_local_load(page, servers):
    """The refactor removed the eager fetch that fired on every local load."""
    calls: list[str] = []
    page.on("request", lambda r: "/fetch-image/" in r.url and calls.append(r.url))
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.wait_for_load_state("networkidle")
    assert calls == [], f"unexpected fetch-image calls: {calls}"


def test_legacy_frontend_had_spurious_fetch_on_local_load(page, servers):
    """Documents the original bug: a spurious /fetch-image/?url=null on load."""
    calls: list[str] = []
    page.on("request", lambda r: "/fetch-image/" in r.url and calls.append(r.url))
    _stub_cdn(page)
    page.goto(servers["legacy"] + "/")
    page.wait_for_load_state("networkidle")
    assert len(calls) >= 1, "expected the legacy eager fetch to fire"


@pytest.mark.parametrize("frontend,expected", [("new", 1), ("legacy", 2)])
def test_eln_load_fetch_count(page, servers, test_image_path, frontend, expected):
    """ELN-load path: the refactor fetches once; the original fetched twice.

    /fetch-image/ and /serve-image/ are stubbed so no real (SSRF-guarded)
    network request is made; only the number of fetch-image calls is compared.
    """
    png = Path(test_image_path).read_bytes()
    calls: list[str] = []

    page.route(
        "**/fetch-image/**",
        lambda route: (calls.append(route.request.url),
                       route.fulfill(status=200, content_type="application/json",
                                     body='{"imagePath": "stub.png"}'))[1],
    )
    page.route(
        "**/serve-image/**",
        lambda route: route.fulfill(status=200, content_type="image/png", body=png),
    )
    _stub_cdn(page)

    page.goto(servers[frontend] + "/?url=http%3A%2F%2Feln.example%2Fplate.png")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    assert len(calls) == expected, f"{frontend}: fetch-image calls={len(calls)} (want {expected})"

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


# Readiness: the new frontend sets body.dataset.imageReady; the legacy frontend
# flips the result heading to "Prepare Annotations".
_IMAGE_READY = (
    "() => document.body.dataset.imageReady === '1' || "
    "(document.getElementById('annotated-heading') || {})"
    ".textContent === 'Prepare Annotations'"
)


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
    page.wait_for_function(_IMAGE_READY)

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


@pytest.mark.parametrize("frontend", ["new", "legacy"])
def test_vertical_label_flows_to_solvent_column(page, servers, test_image_path, frontend):
    """A V-Line label must reach the solvent column — both frontends, same result.

    Exercises the rewritten line-capture + popup path and the backend's
    nearest-line matching end-to-end.
    """
    _stub_cdn(page)
    page.goto(servers[frontend] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 200, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 200, "y": 50})

    # Add a labelled vertical line via the popup.
    page.click("button:has-text('V-Line')")
    canvas.click(position={"x": 120, "y": 150})
    page.fill("#line-text", "LaneA")
    page.click("#line-popup button:has-text('Submit')")

    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 260, "y": 150})

    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    solvent = page.locator("#values-table tbody tr").first.locator("td").first.inner_text().strip()
    assert solvent == "LaneA", f"{frontend}: solvent column was {solvent!r}"


def _load_with_min_max(page, base_url, image_path):
    """Load a local image and place min (y=250) and max (y=50) lines."""
    _stub_cdn(page)
    page.goto(base_url + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", image_path)
    page.wait_for_function(_IMAGE_READY)
    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 50})
    return canvas


def test_drag_spot_changes_rf(page, servers, test_image_path):
    """Dragging a spot upward must raise its RF (0.5 -> ~0.75)."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})  # RF 0.5 before drag

    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 150, box["y"] + 150)
    page.mouse.down()
    page.mouse.move(box["x"] + 150, box["y"] + 100, steps=10)  # drag up ~50px
    page.mouse.up()

    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    rf = float(page.locator("#values-table tbody tr").first.locator("td").nth(2).inner_text())
    assert 0.70 <= rf <= 0.80, f"RF after drag was {rf} (expected ~0.75)"


def test_double_click_deletes_spot(page, servers, test_image_path):
    """Double-clicking a spot removes it (two spots -> one row after delete)."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 110, "y": 150})
    canvas.click(position={"x": 210, "y": 150})

    canvas.dblclick(position={"x": 110, "y": 150})  # delete the first spot

    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    rows = page.locator("#values-table tbody tr").count()
    assert rows == 1, f"expected 1 row after deleting one of two spots, got {rows}"


def test_result_row_highlight_and_delete(page, servers, test_image_path):
    """A result row highlights its spot on click and removes it on delete."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 110, "y": 150})
    canvas.click(position={"x": 210, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr")
    assert page.locator("#values-table tbody tr").count() == 2

    first = page.locator("#values-table tbody tr").first
    first.click()
    assert "active" in (first.get_attribute("class") or ""), "row did not highlight"

    first.locator(".row-del").click()
    page.wait_for_function("() => document.querySelectorAll('#values-table tbody tr').length === 1")


def test_auto_detect_populates_spots(page, servers, blob_image_path):
    """New feature: Auto-detect finds the drawn spots and they flow into RF calc."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 200, "y": 280})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 200, "y": 40})

    page.click("button:has-text('Auto-detect')")
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    rows = page.locator("#values-table tbody tr").count()
    assert rows == 3, f"expected 3 auto-detected spots, table had {rows}"


def test_plate_region_confines_auto_detect(page, servers, blob_image_path):
    """Drawing a plate area limits Auto-detect to that region (2 of 3 blobs)."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 200, "y": 280})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 200, "y": 40})

    # Draw a plate area around the top two blobs (at y=100,150), excluding y=220.
    page.click("button:has-text('Plate Area')")
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 40, box["y"] + 40)
    page.mouse.down()
    page.mouse.move(box["x"] + 260, box["y"] + 190, steps=8)
    page.mouse.up()

    page.click("button:has-text('Auto-detect')")
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    rows = page.locator("#values-table tbody tr").count()
    assert rows == 2, f"expected 2 spots within the region, table had {rows}"


def test_rotate_reorients_and_locks_after_annotation(page, servers, blob_image_path):
    """Rotate re-orients a clean image (layout flips) and is disabled once annotated."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)  # 400x300 landscape
    page.wait_for_function(_IMAGE_READY)

    work_area = page.locator("#work-area")
    assert "layout-rows" in (work_area.get_attribute("class") or "")  # landscape -> rows
    page.click("[data-action='rotateCW']")
    page.wait_for_function(
        "() => document.getElementById('work-area').classList.contains('layout-columns')"
    )  # rotated to portrait -> columns

    # Annotating locks rotation.
    page.click("button:has-text('Add Spots')")
    page.locator("#image-canvas").click(position={"x": 150, "y": 150})
    page.wait_for_function("() => document.querySelector(\"[data-action='rotateCW']\").disabled")


def test_ring_center_preference_persists(page, servers):
    """The 'Center on ring marks' checkbox remembers its state across reloads."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.wait_for_selector("#ring-center")
    assert not page.is_checked("#ring-center")

    page.check("#ring-center")
    page.reload()
    page.wait_for_selector("#ring-center")
    assert page.is_checked("#ring-center"), "ring-center preference was not persisted"


def test_deleting_vline_closes_annotation_popup(page, servers, test_image_path):
    """Deleting a just-drawn V-Line must also dismiss its annotation popup."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)

    page.click("button:has-text('V-Line')")
    page.locator("#image-canvas").click(position={"x": 150, "y": 150})
    # The annotation popup opens for the new line.
    page.wait_for_selector("#line-popup", state="visible")

    # Delete the line via its delete icon; the popup must close.
    page.locator(".delete-icon").first.click()
    page.wait_for_selector("#line-popup", state="hidden")
    assert page.locator(".delete-icon").count() == 0


def test_theme_toggle_switches_and_persists(page, servers):
    """The theme button flips light/dark, overrides the OS default, and persists."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.wait_for_selector("#theme-toggle")

    page.click("#theme-toggle")
    first = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    assert first in ("light", "dark")

    page.click("#theme-toggle")
    second = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    assert second in ("light", "dark") and second != first, "toggle did not flip the theme"

    # Persisted across a reload.
    page.reload()
    page.wait_for_selector("#theme-toggle")
    after = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    assert after == second, f"theme not persisted: {after!r} != {second!r}"


def test_plate_region_suggests_reference_lines(page, servers, blob_image_path):
    """Drawing a plate area suggests Min/Max lines, so RF computes without them."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Plate Area')")
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 30, box["y"] + 30)
    page.mouse.down()
    page.mouse.move(box["x"] + 380, box["y"] + 270, steps=8)
    page.mouse.up()

    # No manual Min/Max lines: they were suggested from the region edges.
    page.click("button:has-text('Auto-detect')")
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert page.locator("#values-table tbody tr").count() == 3


def test_crop_view_zooms_to_plate(page, servers, blob_image_path):
    """The 'crop view' checkbox zooms the work area to the plate box (view-only)."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    box = canvas.bounding_box()
    page.click("button:has-text('Plate Area')")
    page.mouse.move(box["x"] + 40, box["y"] + 40)
    page.mouse.down()
    page.mouse.move(box["x"] + 260, box["y"] + 200, steps=8)
    page.mouse.up()

    container = "() => document.getElementById('canvas-container').style.width"
    assert not page.evaluate(container), "container should be unconstrained before crop"
    page.check("#crop-plate")
    page.wait_for_function(f"{container} !== ''")  # zoomed to region
    page.uncheck("#crop-plate")
    page.wait_for_function(f"{container} === ''")  # back to full view


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
        lambda route: (
            calls.append(route.request.url),
            route.fulfill(
                status=200, content_type="application/json", body='{"imagePath": "stub.png"}'
            ),
        )[1],
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

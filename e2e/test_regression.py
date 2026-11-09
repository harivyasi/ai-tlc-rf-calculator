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

import json
import re
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


# Results-table columns. The legacy frontend has Lane | Compound | Rf; the
# current one # | Lane | Lane type | Compound | Compound type | Rf.
_COLUMNS = {
    "new": {"n": 0, "lane": 1, "lane_type": 2, "compound": 3, "compound_type": 4, "rf": 5},
    "legacy": {"lane": 0, "compound": 1, "rf": 2},
}


def _cell(page: Page, column: str, row: int = 0, frontend: str = "new") -> str:
    """Text of one results-table cell, by column name."""
    cells = page.locator("#values-table tbody tr").nth(row).locator("td")
    return cells.nth(_COLUMNS[frontend][column]).inner_text().strip()


def _load_local_image_and_calculate(
    page: Page, base_url: str, image_path: str, frontend: str = "new"
) -> str:
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
    return _cell(page, "rf", frontend=frontend)


@pytest.mark.parametrize("frontend", ["new", "legacy"])
def test_local_upload_rf_matches(page, servers, test_image_path, frontend):
    """Core regression: both frontends compute the same RF via the new backend."""
    rf = _load_local_image_and_calculate(page, servers[frontend], test_image_path, frontend)
    assert float(rf) == 0.5, f"{frontend} frontend produced RF={rf!r}"


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
    solvent = _cell(page, "lane", frontend=frontend)
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
    rf = float(_cell(page, "rf"))
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


def test_deleting_result_row_marks_results_stale(page, servers, test_image_path):
    """Deleting a row makes the annotated image out of date: it must flag the
    results stale (banner + disabled export), not silently keep the old image."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 110, "y": 150})
    canvas.click(position={"x": 210, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert not page.locator("#stale-banner").is_visible()
    assert not page.locator("#export-row .lab-btn").first.is_disabled()

    page.locator("#values-table tbody tr").first.locator(".row-del").click()
    page.wait_for_selector("#stale-banner", state="visible")
    assert page.locator("#export-row .lab-btn").first.is_disabled(), (
        "export not blocked while stale"
    )
    assert page.locator("#values-table tbody tr").count() == 1


def test_spot_sources_reach_the_results(page, servers, test_image_path):
    """Each spot is recorded as manual, detected, or detected then moved."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    scale = page.evaluate("window.scaleFactor")
    candidates = [
        {"x": 150 * scale, "y": 150 * scale, "conf": 0.6},
        {"x": 80 * scale, "y": 120 * scale, "conf": 0.5},
    ]
    page.route(
        "**/detect-spots/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"spots": candidates, "count": 2, "roi": [0, 0, 1, 1], "model": {}}),
        ),
    )
    with page.expect_response("**/detect-spots/"):
        page.click("#detect-spots-btn")
    page.wait_for_selector("text=Detected 2 spot(s)")
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 220, "y": 180})  # manual

    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 150, box["y"] + 150)
    page.mouse.down()
    page.mouse.move(box["x"] + 150, box["y"] + 110, steps=10)  # move a detected spot
    page.mouse.up()

    with page.expect_response("**/calculate_rf/") as response_info:
        page.click("button:has-text('Calculate RF')")
    rows = response_info.value.json()["rf_values"]
    assert sorted(row["source"] for row in rows) == ["detected", "detected, moved", "manual"]


def test_auto_detect_populates_spots(page, servers, blob_image_path):
    """Detect Spots (TLC-SpotDet) finds the drawn spots between Min and Max, and
    they flow into the RF calculation."""
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

    page.click("button:has-text('Detect Spots')")
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    rows = page.locator("#values-table tbody tr").count()
    assert rows == 3, f"expected 3 auto-detected spots, table had {rows}"


def test_plate_region_confines_auto_detect(page, servers, blob_image_path):
    """Drawing a plate area limits Detect Spots to that region (2 of 3 blobs)."""
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

    page.click("button:has-text('Detect Spots')")
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    rows = page.locator("#values-table tbody tr").count()
    assert rows == 2, f"expected 2 spots within the region, table had {rows}"


def test_rotate_reorients_and_locks_after_annotation(page, servers, blob_image_path):
    """A preset re-orients a clean image (layout flips); annotating locks rotation."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)  # 400x300 landscape
    page.wait_for_function(_IMAGE_READY)

    work_area = page.locator("#work-area")
    assert "layout-rows" in (work_area.get_attribute("class") or "")  # landscape -> rows
    page.select_option("#rotation-preset", "90")
    page.wait_for_function(
        "() => document.getElementById('work-area').classList.contains('layout-columns')"
    )  # rotated to portrait -> columns

    # Annotating locks rotation.
    page.click("button:has-text('Add Spots')")
    page.locator("#image-canvas").click(position={"x": 150, "y": 150})
    page.wait_for_function("() => document.getElementById('rotation-preset').disabled")
    assert page.locator("#rotation-fine").is_disabled()
    assert page.locator(".rotation-nudge").first.is_disabled()


def _dot_image(tmp_path, size=(400, 300), at=(300, 80)):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    x, y = at
    ImageDraw.Draw(image).ellipse((x - 6, y - 6, x + 6, y + 6), fill="black")
    path = tmp_path / "dot.png"
    image.save(path)
    return str(path)


_DARK_CENTROID = """() => {
    const c = document.getElementById('image-canvas');
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let sx = 0, sy = 0, n = 0;
    for (let i = 0; i < d.length; i += 4) {
        if (d[i] < 128) { const p = i / 4; sx += p % c.width; sy += Math.floor(p / c.width); n++; }
    }
    return {x: sx / n + 0.5, y: sy / n + 0.5, n, w: c.width, h: c.height};
}"""


def test_fine_rotation_turns_the_canvas_like_the_server(page, servers, tmp_path):
    """The canvas turns about its centre, keeping its size -- the same formula
    the server's _fine_rotate is tested against -- and the angle is sent."""
    import math

    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", _dot_image(tmp_path))
    page.wait_for_function(_IMAGE_READY)

    page.fill("#rotation-fine", "12")
    page.dispatch_event("#rotation-fine", "change")
    dot = page.evaluate(_DARK_CENTROID)
    assert (dot["w"], dot["h"]) == (400, 300)  # same frame
    t = math.radians(12)
    dx, dy = 300.5 - 200, 80.5 - 150
    assert abs(dot["x"] - (200 + dx * math.cos(t) - dy * math.sin(t))) < 1.0
    assert abs(dot["y"] - (150 + dx * math.sin(t) + dy * math.cos(t))) < 1.0

    # Nudges step by 0.5 degrees; the angle travels with requests.
    page.click("[data-action='nudgeRotation'][data-arg='0.5']")
    assert page.input_value("#rotation-fine") == "12.5"
    with page.expect_request("**/detect-lines/") as request_info:
        page.click("#detect-lines-btn")
    body = request_info.value.post_data_json
    assert (body["rotation"], body["fineRotation"]) == (0, 12.5)


def test_fine_rotation_is_clamped(page, servers, blob_image_path):
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)
    page.fill("#rotation-fine", "80")
    page.dispatch_event("#rotation-fine", "change")
    assert page.input_value("#rotation-fine") == "45.0"


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


def test_plate_area_does_not_create_reference_lines(page, servers, blob_image_path):
    """A Plate Area is a detection region only — it must NOT create Min/Max lines
    (the plate edges are not the baseline / solvent front). Without them neither
    Detect Spots nor Calculate RF is available; once placed, both work."""
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

    assert page.locator("#detect-spots-btn").is_disabled(), (
        "plate area must not seed Min/Max — Detect Spots should stay disabled"
    )
    assert page.locator("#calculate-btn").is_disabled()

    # Placing Min/Max by hand (inside the plate area, which confines them)
    # enables detection and the calculation.
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 200, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 200, "y": 45})
    page.click("button:has-text('Detect Spots')")
    page.wait_for_selector("text=/Detected \\d+ spot/", timeout=20000)
    page.wait_for_function("() => !document.getElementById('calculate-btn').disabled")
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert page.locator("#values-table tbody tr").count() == 3


def test_detect_spots_needs_min_and_max(page, servers, blob_image_path):
    """Shilpa's workflow: spot inference is restricted to the Min/Max band, so
    Detect Spots is disabled until both lines exist, while Detect Lines is
    available as soon as an image is loaded."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    assert page.locator("#detect-lines-btn").is_disabled()  # no image yet
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    assert not page.locator("#detect-lines-btn").is_disabled()
    assert page.locator("#detect-spots-btn").is_disabled()
    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 200, "y": 280})
    assert page.locator("#detect-spots-btn").is_disabled(), "Max is still missing"
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 200, "y": 40})
    page.wait_for_function("() => !document.getElementById('detect-spots-btn').disabled")

    # The request carries the band.
    with page.expect_request("**/detect-spots/") as request_info:
        page.click("button:has-text('Detect Spots')")
    body = request_info.value.post_data_json
    assert body["minY"] > body["maxY"], body


def _route_detect_lines(page: Page, body: dict) -> None:
    """Answer /detect-lines/ with ``body``: the model needs a real plate photo
    to find lines, so the browser side is tested against a stubbed response."""
    import json

    page.route(
        "**/detect-lines/",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        ),
    )


def test_detect_lines_places_min_and_max(page, servers, blob_image_path):
    """Detect Lines places editable Min/Max lines (one undo step), after which
    Detect Spots and Calculate RF work as with hand-placed lines."""
    _stub_cdn(page)
    _route_detect_lines(page, {"minY": 280, "maxY": 40, "estimated": None, "model": {}})
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    page.click("#detect-lines-btn")
    page.wait_for_selector("text=Placed Min and Max", timeout=10000)
    lines = page.evaluate(
        "() => import('/static/tlc/js/state.js').then(m => "
        "m.state.lines.map(l => [l.type, Math.round(l.y)]))"
    )
    assert sorted(lines) == [["max", 40], ["min", 280]], lines

    page.click("button:has-text('Detect Spots')")
    page.wait_for_selector("text=/Detected \\d+ spot/", timeout=20000)
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert page.locator("#values-table tbody tr").count() == 3

    # Both lines came from one detection, so a single undo removes both.
    page.click("#undo-btn")
    page.click("#undo-btn")  # the spot batch, then the line pair
    remaining = page.evaluate(
        "() => import('/static/tlc/js/state.js').then(m => m.state.lines.length)"
    )
    assert remaining == 0, remaining


def test_detect_lines_warns_when_one_line_is_estimated(page, servers, blob_image_path):
    _stub_cdn(page)
    _route_detect_lines(page, {"minY": 270, "maxY": 30.0, "estimated": "max", "model": {}})
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    page.click("#detect-lines-btn")
    page.wait_for_selector("text=is an estimate", timeout=10000)


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


# --- Touch / pointer support ------------------------------------------------
# These use a touch-enabled browser context (the default `page` fixture has
# none). They exercise the Pointer Events path: a tap adds a spot, a touch drag
# moves one, and a double-tap deletes one.


def _touch_page(browser, servers, image_path):
    """A touch-enabled page loaded with an image and min/max lines."""
    context = browser.new_context(viewport={"width": 1600, "height": 1200}, has_touch=True)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    canvas = _load_with_min_max(page, servers["new"], image_path)
    return context, page, canvas, errors


def test_touch_tap_adds_and_double_tap_deletes_spot(browser, servers, test_image_path):
    """Touch: two taps add two spots; a double-tap removes one (1 row remains)."""
    context, page, canvas, errors = _touch_page(browser, servers, test_image_path)
    try:
        box = canvas.bounding_box()
        page.click("button:has-text('Add Spots')")
        page.touchscreen.tap(box["x"] + 110, box["y"] + 150)  # add spot A
        page.touchscreen.tap(box["x"] + 210, box["y"] + 150)  # add spot B
        page.touchscreen.tap(box["x"] + 110, box["y"] + 150)  # 1st tap on A
        page.touchscreen.tap(box["x"] + 110, box["y"] + 150)  # 2nd tap -> delete A

        page.click("button:has-text('Calculate RF')")
        page.wait_for_selector("#values-table tbody tr", timeout=20000)
        rows = page.locator("#values-table tbody tr").count()
        assert rows == 1, f"expected 1 spot after tap-add x2 + double-tap delete, got {rows}"
        assert not errors, f"JS errors: {errors}"
    finally:
        context.close()


def test_touch_drag_moves_spot(browser, servers, test_image_path):
    """Touch drag (synthetic touch PointerEvents) raises a spot's RF 0.5 -> ~0.75."""
    context, page, canvas, errors = _touch_page(browser, servers, test_image_path)
    try:
        page.click("button:has-text('Add Spots')")
        page.touchscreen.tap(canvas.bounding_box()["x"] + 150, canvas.bounding_box()["y"] + 150)

        # A touch drag from y=150 up to y=100 via the pointer handlers directly.
        page.evaluate(
            """({y0, y1}) => {
                const c = document.getElementById('image-canvas');
                const r = c.getBoundingClientRect();
                const pe = (t, cy) => new PointerEvent(t, {
                    pointerId: 1, pointerType: 'touch', bubbles: true,
                    clientX: r.left + 150, clientY: r.top + cy,
                });
                c.dispatchEvent(pe('pointerdown', y0));
                c.dispatchEvent(pe('pointermove', (y0 + y1) / 2));
                c.dispatchEvent(pe('pointermove', y1));
                c.dispatchEvent(pe('pointerup', y1));
            }""",
            {"y0": 150, "y1": 100},
        )

        page.click("button:has-text('Calculate RF')")
        page.wait_for_selector("#values-table tbody tr", timeout=20000)
        rf = float(_cell(page, "rf"))
        assert 0.70 <= rf <= 0.80, f"RF after touch drag was {rf} (expected ~0.75)"
        assert not errors, f"JS errors: {errors}"
    finally:
        context.close()


def test_text_scale_is_sent_in_payload_and_persists(page, servers, test_image_path):
    """The annotation text-size % flows into the calculate_rf payload (as a
    fraction) and the preference survives a reload."""
    sent: list[str | None] = []
    page.on(
        "request",
        lambda r: sent.append(r.post_data) if r.url.endswith("/calculate_rf/") else None,
    )
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.fill("#text-scale", "250")
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)

    bodies = [b.replace(" ", "") for b in sent if b]
    assert any('"textScale":2.5' in b for b in bodies), f"textScale missing/wrong: {bodies}"

    page.reload()
    page.wait_for_selector("#text-scale")
    assert page.locator("#text-scale").input_value() == "250", "text-size preference not persisted"


def test_changing_text_size_marks_results_stale(page, servers, test_image_path):
    """Changing the text size (burned into the annotated image) flags a prior
    result stale — the canvas preview updates live, but export is blocked until
    recalculation."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert not page.locator("#stale-banner").is_visible()

    page.fill("#text-scale", "300")
    page.wait_for_selector("#stale-banner", state="visible")
    assert page.locator("#export-row .lab-btn").first.is_disabled()


def test_editing_after_calc_marks_stale_then_recalc_clears(page, servers, test_image_path):
    """Adding a spot after a calculation flags results stale (banner + pulsing
    Calculate RF + blocked export); recalculating clears it."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert not page.locator("#stale-banner").is_visible()

    canvas.click(position={"x": 220, "y": 150})  # add another spot
    page.wait_for_selector("#stale-banner", state="visible")
    assert page.locator("#export-row .lab-btn").first.is_disabled()
    assert "pulse" in (page.locator("#calculate-btn").get_attribute("class") or "")

    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#stale-banner", state="hidden")
    assert not page.locator("#export-row .lab-btn").first.is_disabled()


# --- Additional UI-behaviour coverage --------------------------------------


def test_reset_clears_annotations_and_disables_calculate(page, servers, test_image_path):
    """Reset empties the results table and re-disables Calculate RF."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)

    page.click("button:has-text('Reset')")
    page.wait_for_function("() => document.querySelectorAll('#values-table tbody tr').length === 0")
    assert page.locator("#calculate-btn").is_disabled(), "Calculate should be disabled after reset"


def test_horizontal_label_flows_to_compound_column(page, servers, test_image_path):
    """An H-Line label reaches the compound column (mirrors the V-Line path)."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 50})
    page.click("button:has-text('H-Line')")
    canvas.click(position={"x": 150, "y": 150})
    page.fill("#line-text", "CompoundZ")
    page.click("#line-popup button:has-text('Submit')")
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 180})  # off the label line so it isn't a line drag

    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    compound = _cell(page, "compound")
    assert compound == "CompoundZ", f"compound column was {compound!r}"


def test_calculate_button_gates_on_required_annotations(page, servers, test_image_path):
    """Calculate RF stays disabled until min + max + at least one spot exist."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)
    assert page.locator("#calculate-btn").is_disabled(), "disabled with image only"

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 50})
    assert page.locator("#calculate-btn").is_disabled(), "still disabled without a spot"

    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.wait_for_function("() => !document.getElementById('calculate-btn').disabled")


def test_help_drawer_toggles_open_and_closed(page, servers, test_image_path):
    """The Help button opens and closes the help drawer via its visible close button."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.locator("[data-action='toggleHelp']").first.click()  # toolbar Help button
    page.wait_for_function(
        "() => document.getElementById('help-drawer').classList.contains('open')"
    )
    # The close button must be a visible control (regression: it was white-on-white,
    # so the user got stuck with no way out).
    close_btn = page.locator("#help-drawer .help-close")
    assert close_btn.is_visible(), "help drawer has no visible close button"
    close_btn.click()
    page.wait_for_function(
        "() => !document.getElementById('help-drawer').classList.contains('open')"
    )


def test_escape_closes_help_drawer(page, servers, test_image_path):
    """Pressing Escape closes the help drawer (it was previously inescapable)."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.locator("[data-action='toggleHelp']").first.click()
    page.wait_for_function(
        "() => document.getElementById('help-drawer').classList.contains('open')"
    )
    page.keyboard.press("Escape")
    page.wait_for_function(
        "() => !document.getElementById('help-drawer').classList.contains('open')"
    )


def test_escape_closes_line_popup(page, servers, test_image_path):
    """Pressing Escape hides the open line-label popup (keydown handler)."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('V-Line')")
    canvas.click(position={"x": 120, "y": 150})
    page.wait_for_selector("#line-popup", state="visible")
    page.keyboard.press("Escape")
    page.wait_for_selector("#line-popup", state="hidden")


def test_image_source_collapses_on_load_and_reexpands(page, servers, test_image_path):
    """The source card collapses once an image loads and re-expands on click."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)
    page.wait_for_function(
        "() => document.getElementById('source-body').classList.contains('hidden')"
    )

    page.click("#source-change")  # the "Change" button re-expands the source card
    page.wait_for_function(
        "() => !document.getElementById('source-body').classList.contains('hidden')"
    )


def test_line_delete_icon_stays_on_scaled_canvas(page, servers, wide_image_path):
    """A reference line's delete cross must sit on the line even when the canvas
    is CSS-scaled — a low Min/Max line previously pushed its × off the canvas."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", wide_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    scaled = page.evaluate(
        "() => { const c = document.getElementById('image-canvas');"
        " return c.clientWidth < c.width; }"
    )
    assert scaled, "canvas is not CSS-scaled here; the test would not exercise the bug"

    cb = canvas.bounding_box()
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": cb["width"] * 0.5, "y": cb["height"] * 0.92})  # low line

    icon = page.locator(".delete-icon").first
    icon.wait_for(state="visible")
    ib = icon.bounding_box()
    icy = ib["y"] + ib["height"] / 2
    expected = cb["y"] + cb["height"] * 0.92
    assert cb["y"] - 15 <= icy <= cb["y"] + cb["height"] + 15, (
        "delete cross fell outside the canvas"
    )
    assert abs(icy - expected) < 25, (
        f"delete cross not on the line (icon y={icy:.0f}, line y≈{expected:.0f})"
    )


# --- Confining annotations to the plate area --------------------------------


def _draw_region(page, canvas, fx0, fy0, fx1, fy1):
    """Arm Plate Area and drag a box given as canvas fractions."""
    box = canvas.bounding_box()
    page.click("button:has-text('Plate Area')")
    page.mouse.move(box["x"] + box["width"] * fx0, box["y"] + box["height"] * fy0)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * fx1, box["y"] + box["height"] * fy1, steps=6)
    page.mouse.up()


def test_min_line_click_blocked_outside_plate_area(page, servers, blob_image_path):
    """With a plate area, a Min-line click above it places nothing; inside works."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    _draw_region(page, canvas, 0.15, 0.30, 0.85, 0.85)  # box in the lower area

    page.click("button:has-text('Min Line')")
    canvas.click(
        position={
            "x": canvas.bounding_box()["width"] * 0.5,
            "y": canvas.bounding_box()["height"] * 0.05,
        }
    )
    # A line places a .delete-icon; a blocked click places none.
    assert page.locator(".delete-icon").count() == 0, "min line was placed outside the plate area"

    canvas.click(
        position={
            "x": canvas.bounding_box()["width"] * 0.5,
            "y": canvas.bounding_box()["height"] * 0.6,
        }
    )
    page.wait_for_function("() => document.querySelectorAll('.delete-icon').length === 1")


def test_spot_click_blocked_outside_plate_area(page, servers, blob_image_path):
    """With a plate area, an Add-Spots click outside it adds no spot."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    _draw_region(page, canvas, 0.15, 0.30, 0.85, 0.85)
    w = canvas.bounding_box()["width"]
    h = canvas.bounding_box()["height"]
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": w * 0.5, "y": h * 0.8})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": w * 0.5, "y": h * 0.35})

    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": w * 0.5, "y": h * 0.05})  # above the box -> blocked
    assert page.locator("#calculate-btn").is_disabled(), "spot was added outside the plate area"

    canvas.click(position={"x": w * 0.5, "y": h * 0.6})  # inside -> added
    page.wait_for_function("() => !document.getElementById('calculate-btn').disabled")


def test_redraw_deletes_spots_outside_and_keeps_inside(page, servers, blob_image_path):
    """Redrawing a smaller plate area deletes spots outside the new box (lines clamp)."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    w = canvas.bounding_box()["width"]
    h = canvas.bounding_box()["height"]

    _draw_region(page, canvas, 0.08, 0.08, 0.92, 0.95)  # big box covering everything
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": w * 0.5, "y": h * 0.9})  # baseline near bottom
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": w * 0.5, "y": h * 0.12})  # front near top
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": w * 0.3, "y": h * 0.75})  # lower spot (kept)
    canvas.click(position={"x": w * 0.7, "y": h * 0.20})  # upper spot (will be dropped)

    # Redraw a smaller box covering only the lower half; the upper spot is dropped,
    # the Max line clamps down to the new top edge.
    _draw_region(page, canvas, 0.08, 0.50, 0.92, 0.95)

    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert page.locator("#values-table tbody tr").count() == 1, (
        "spot outside the redrawn area was not dropped"
    )


def test_drag_line_is_clamped_to_plate_area(page, servers, blob_image_path):
    """Dragging a Min line below the plate area stops it at the bottom edge."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", blob_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    box = canvas.bounding_box()
    _draw_region(page, canvas, 0.2, 0.2, 0.8, 0.7)  # region bottom at 0.7 of the canvas

    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": box["width"] * 0.5, "y": box["height"] * 0.5})  # inside

    # Drag the line well below the plate bottom; it must clamp to the 0.7 edge.
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.98, steps=10)
    page.mouse.up()

    icon = page.locator(".delete-icon").first
    icon.wait_for(state="visible")
    ib = icon.bounding_box()
    icy = ib["y"] + ib["height"] / 2
    edge = box["y"] + box["height"] * 0.7  # plate-area bottom, in screen px
    assert abs(icy - edge) < 15, (
        f"min line not clamped to plate bottom (icon y={icy:.0f}, edge={edge:.0f})"
    )


# --- Undo / redo ------------------------------------------------------------


def test_undo_redo_spot_edits(page, servers, test_image_path):
    """Undo removes the last edit and redo re-applies it; recalculating reflects
    the current spots. Undo/redo buttons enable/disable correctly."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    # Placing Min + Max already produced undoable history.
    assert not page.locator("#undo-btn").is_disabled()
    assert page.locator("#redo-btn").is_disabled()

    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 130, "y": 150})
    canvas.click(position={"x": 210, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert page.locator("#values-table tbody tr").count() == 2

    # Undo drops the second spot; recalculate -> 1 row.
    page.click("#undo-btn")
    assert not page.locator("#redo-btn").is_disabled()
    page.click("button:has-text('Calculate RF')")
    page.wait_for_function("() => document.querySelectorAll('#values-table tbody tr').length === 1")

    # Redo brings it back; recalculate -> 2 rows.
    page.click("#redo-btn")
    page.click("button:has-text('Calculate RF')")
    page.wait_for_function("() => document.querySelectorAll('#values-table tbody tr').length === 2")


def test_undo_keyboard_shortcut(page, servers, test_image_path):
    """Ctrl+Z undoes the last edit (a placed spot)."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 130, "y": 150})
    canvas.click(position={"x": 210, "y": 150})
    page.keyboard.press("Control+z")  # undo the 2nd spot
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert page.locator("#values-table tbody tr").count() == 1


def test_unmarked_lane_blank_compound_unknown_in_table(page, servers, test_image_path):
    """With no V-Line/H-Line, the results table shows a blank lane (Mixture) and
    an "Unknown" compound (Substance) — the lane name is not invented."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert _cell(page, "lane") == "", "unmarked lane should be blank"
    assert _cell(page, "compound") == "Unknown"


# --- Export buttons: download pair vs ELN-upload pair -----------------------

_EXPORT_VISIBLE = (
    "() => Array.from(document.querySelectorAll('#export-row .lab-btn'))"
    ".filter((b) => b.offsetParent !== null).length"
)


def test_export_local_shows_only_download_pair(page, servers, test_image_path):
    """A local upload shows just the three download buttons (the ELN uploads
    are hidden)."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)
    assert page.evaluate(_EXPORT_VISIBLE) == 3
    assert not page.locator("#export-row .export-eln").first.is_visible()


def test_export_eln_shows_all_six(page, servers, test_image_path):
    """An ELN-sourced plate shows all six buttons (download + upload × CSV,
    Image, Annotations)."""
    png = Path(test_image_path).read_bytes()
    page.route(
        "**/fetch-image/**",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"imagePath": "stub.png"}'
        ),
    )
    page.route(
        "**/serve-image/**",
        lambda route: route.fulfill(status=200, content_type="image/png", body=png),
    )
    _stub_cdn(page)
    page.goto(servers["new"] + "/?url=http%3A%2F%2Feln.example%2Fplate.png")
    page.wait_for_function(_IMAGE_READY)
    assert page.evaluate(_EXPORT_VISIBLE) == 6


def test_eln_upload_names_the_file_and_sends_no_csrf_token(page, servers, test_image_path):
    """Results go to the ELN's token URL with a readable `attachmentName`, and
    without our CSRF token (meaningless there, and it forces a preflight)."""
    png = Path(test_image_path).read_bytes()
    page.route(
        "**/fetch-image/**",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"imagePath": "stub.png"}'
        ),
    )
    page.route(
        "**/serve-image/**",
        lambda route: route.fulfill(status=200, content_type="image/png", body=png),
    )
    result = {
        "rf_values": [
            {
                "n": 1,
                "x": 150,
                "y": 150,
                "rf": 0.5,
                "solvent": "",
                "solvent_type": "",
                "compound": "Unknown",
                "compound_type": "",
            }
        ],
        "annotated_image_url": "/media/stub-annotated.png",
        "csv_url": "/media/stub.csv",
    }
    page.route(
        "**/calculate_rf/**",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(result)
        ),
    )
    page.route(
        "**/media/stub.csv",
        lambda route: route.fulfill(status=200, content_type="text/csv", body="spot\n1\n"),
    )
    page.route(
        "**/media/stub-annotated.png",
        lambda route: route.fulfill(status=200, content_type="image/png", body=png),
    )
    uploads = []

    def eln(route):
        uploads.append(route.request)
        route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            body='{"message": "File uploaded successfully"}',
        )

    page.route("http://eln.example/**", eln)
    _stub_cdn(page)
    page.goto(servers["new"] + "/?url=http%3A%2F%2Feln.example%2Fplate.png")
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 50})
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr")
    page.click("[data-action='csvToEln']")
    page.wait_for_selector("text=Saved to ELN")

    assert len(uploads) == 1
    request = uploads[0]
    assert request.method == "POST"
    assert "x-csrftoken" not in {k.lower() for k in request.headers}
    body = request.post_data_buffer.decode("utf-8", "replace")
    assert re.search(r'name="attachmentName"\r\n\r\nTLC_Rf_\d{4}-\d\d-\d\d_\d\d-\d\d\.csv', body)


def test_eln_upload_failure_is_reported(page, servers, test_image_path):
    """A refused upload (e.g. the token's upload limit) says so, not "Saved"."""
    png = Path(test_image_path).read_bytes()
    page.route(
        "**/fetch-image/**",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"imagePath": "stub.png"}'
        ),
    )
    page.route(
        "**/serve-image/**",
        lambda route: route.fulfill(status=200, content_type="image/png", body=png),
    )
    page.route(
        "**/calculate_rf/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "rf_values": [
                        {
                            "n": 1,
                            "x": 150,
                            "y": 150,
                            "rf": 0.5,
                            "solvent": "",
                            "solvent_type": "",
                            "compound": "Unknown",
                            "compound_type": "",
                        }
                    ],
                    "annotated_image_url": "/media/a.png",
                    "csv_url": "/media/s.csv",
                }
            ),
        ),
    )
    page.route("**/media/s.csv", lambda route: route.fulfill(status=200, body="spot\n"))
    page.route(
        "http://eln.example/**",
        lambda route: route.fulfill(
            status=403, headers={"Access-Control-Allow-Origin": "*"}, body="{}"
        ),
    )
    _stub_cdn(page)
    page.goto(servers["new"] + "/?url=http%3A%2F%2Feln.example%2Fplate.png")
    page.wait_for_function(_IMAGE_READY)
    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 50})
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr")
    page.click("[data-action='csvToEln']")
    page.wait_for_selector("text=Failed to save to ELN")


def test_min_above_max_is_explained_not_computed(page, servers, test_image_path):
    """SI Eq. 4: the baseline must lie below the solvent front. Swapped lines
    get the server's explanation instead of a generic failure."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 50})  # Min near the top: wrong
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("text=baseline below", timeout=10000)
    assert page.locator("#values-table tbody tr").count() == 0


def test_detect_lines_is_confined_to_the_plate_area(page, servers, blob_image_path):
    """With a Plate Area drawn, Detect Lines sends it, so the model only sees
    the plate (photos with much background otherwise get a line on it)."""
    _stub_cdn(page)
    _route_detect_lines(page, {"minY": 250, "maxY": 60, "estimated": None, "model": {}})
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

    with page.expect_request("**/detect-lines/") as request_info:
        page.click("#detect-lines-btn")
    region = request_info.value.post_data_json.get("region")
    assert region and region["width"] > 300 and region["height"] > 200, region


# --- Reviewer items 4-9 -----------------------------------------------------

_LINES = (
    "() => import('/static/tlc/js/state.js')"
    ".then(m => m.state.lines.map(l => [l.type, l.text, l.kind]))"
)


def _add_marker(page, canvas, button, x, y, text="", kind=""):
    page.click(f"button:has-text('{button}')")
    canvas.click(position={"x": x, "y": y})
    if kind:
        page.select_option("#line-kind", kind)
    page.fill("#line-text", text)
    page.click("#line-popup button:has-text('Submit')")


def test_min_max_buttons_hide_once_placed(page, servers, test_image_path):
    """Item 4: Min/Max are singletons, moved by dragging, so their buttons go
    away once placed and come back if the line is deleted (or undone)."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)
    canvas = page.locator("#image-canvas")

    page.click("#min-line-btn")
    canvas.click(position={"x": 150, "y": 250})
    assert page.locator("#min-line-btn").is_hidden()
    assert page.locator("#max-line-btn").is_visible()
    page.click("#max-line-btn")
    canvas.click(position={"x": 150, "y": 50})
    assert page.locator("#max-line-btn").is_hidden()

    page.locator(".delete-icon:not(.edit-icon)").first.click()  # the Min line's cross
    page.wait_for_selector("#min-line-btn", state="visible")
    page.click("#undo-btn")
    page.wait_for_selector("#min-line-btn", state="hidden")


def test_h_line_is_drawn_black(page, servers, test_image_path):
    """Item 5: the H-line was purple, which read as grey on green plates."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    _add_marker(page, canvas, "H-Line", 150, 150, "SM")
    # Darkest pixel in a short column across the line (the click may land a
    # pixel off, and the line's edges are anti-aliased against its halo).
    darkest = page.evaluate(
        "() => { const d = document.getElementById('image-canvas').getContext('2d')"
        ".getImageData(200, 140, 1, 20).data; let m = 255;"
        " for (let i = 0; i < d.length; i += 4) m = Math.min(m, Math.max(d[i], d[i+1], d[i+2]));"
        " return m; }"
    )
    assert darkest < 40, f"darkest H-line pixel is {darkest}, expected black"


def test_label_types_reach_table_and_can_be_edited(page, servers, test_image_path):
    """Items 6 and 7: a type from the dropdown plus free text; the label can be
    edited afterwards (pencil icon), and Cancel on an edit changes nothing."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    _add_marker(page, canvas, "H-Line", 150, 150, "SM-1", "starting material")
    _add_marker(page, canvas, "V-Line", 100, 150, "R1", "product 1")
    options = page.locator("#line-kind option").all_inner_texts()
    assert "product 2" in options and "expected product" not in options, options

    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 115, "y": 170})  # beside the V-line, not on it
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert (_cell(page, "compound"), _cell(page, "compound_type")) == ("SM-1", "starting material")
    assert (_cell(page, "lane"), _cell(page, "lane_type")) == ("R1", "product 1")

    # Edit the H-line: the popup opens pre-filled; changing it marks results stale.
    page.locator(".edit-icon").first.click()
    assert page.input_value("#line-text") == "SM-1"
    assert page.input_value("#line-kind") == "starting material"
    page.select_option("#line-kind", "confirmed product")
    page.fill("#line-text", "SM-2")
    page.click("#line-popup button:has-text('Submit')")
    page.wait_for_selector("#stale-banner", state="visible")
    assert ["horizontal", "SM-2", "confirmed product"] in page.evaluate(_LINES)

    # Undo restores the previous label, type included.
    page.click("#undo-btn")
    assert ["horizontal", "SM-1", "starting material"] in page.evaluate(_LINES)

    # Cancel on an edit leaves the label as it was.
    page.locator(".edit-icon").first.click()
    page.fill("#line-text", "discarded")
    page.click("#line-popup button:has-text('Cancel')")
    assert ["horizontal", "SM-1", "starting material"] in page.evaluate(_LINES)


def test_table_is_numbered_sorted_by_lane_and_compact(page, servers, test_image_path):
    """Item 9: rows sorted by V-line label (natural order), then Rf; numbered
    like the image; no Status column; type columns hidden when unused; the
    table is as wide as its content."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    _add_marker(page, canvas, "V-Line", 250, 150, "Lane 10")
    _add_marker(page, canvas, "V-Line", 60, 150, "Lane 2")
    page.click("button:has-text('Add Spots')")
    for x, y in ((265, 150), (75, 100), (75, 200)):  # beside the V-lines, not on them
        canvas.click(position={"x": x, "y": y})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr >> nth=2", timeout=20000)

    rows = [(_cell(page, "n", i), _cell(page, "lane", i), _cell(page, "rf", i)) for i in range(3)]
    assert rows == [("1", "Lane 2", "0.25"), ("2", "Lane 2", "0.75"), ("3", "Lane 10", "0.50")]
    headers = page.locator("#values-table thead th:visible").all_inner_texts()
    assert "Status" not in headers and "Lane type" not in headers, headers
    table_w = page.locator("#values-table").bounding_box()["width"]
    panel_w = page.locator(".table-scroll").bounding_box()["width"]
    assert table_w < panel_w, (table_w, panel_w)


def test_csv_download_is_the_server_file(page, servers, test_image_path):
    """The CSV comes from the server, with the table's columns in the table's
    order, the CHMO header line, and labels properly quoted."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    _add_marker(page, canvas, "V-Line", 150, 150, "A, crude", "product 1")
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 165, "y": 100})  # beside the V-line, not on it
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)

    with page.expect_download() as info:
        page.click("[data-action='saveCSV']")
    text = Path(info.value.path()).read_text(encoding="utf-8")
    assert text.startswith("Measurement type;thin layer chromatography (TLC);CHMO:0001007\n")
    assert '1,"A, crude",product 1,Unknown,,0.75' in text, text


def test_full_labels_switch_reaches_the_server(page, servers, test_image_path):
    """Item 8: numbers only by default; "full labels" asks for Rf, compound and
    lane next to each number. It changes the saved image, so results go stale."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    with page.expect_request("**/calculate_rf/") as info:
        page.click("button:has-text('Calculate RF')")
    assert info.value.post_data_json["labelMode"] == "numbers"
    page.wait_for_selector("#values-table tbody tr", timeout=20000)

    page.check("#label-full")
    page.wait_for_selector("#stale-banner", state="visible")
    with page.expect_request("**/calculate_rf/") as info:
        page.click("button:has-text('Calculate RF')")
    assert info.value.post_data_json["labelMode"] == "full"


# --- Local image uploaded once -------------------------------------------------


def test_local_image_is_uploaded_once_then_referenced(page, servers, test_image_path):
    """The image travels once; detection and calculation send only its name."""
    uploads, bodies = [], []
    page.on("request", lambda r: "/upload-image/" in r.url and uploads.append(r))
    page.on(
        "request",
        lambda r: (
            any(p in r.url for p in ("/calculate_rf/", "/detect-"))
            and bodies.append(r.post_data_json)
        ),
    )
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Detect Spots')")
    page.wait_for_selector("text=/spot/i", timeout=20000)
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)

    assert len(uploads) == 1
    assert len(bodies) == 2
    for body in bodies:
        assert body["imageData"] is None and body["imageSrc"].endswith(".png"), body
    assert float(_cell(page, "rf")) == 0.5


def test_failed_upload_falls_back_to_sending_the_image(page, servers, test_image_path):
    page.route("**/upload-image/", lambda route: route.fulfill(status=500, body="{}"))
    bodies = []
    page.on("request", lambda r: "/calculate_rf/" in r.url and bodies.append(r.post_data_json))
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr", timeout=20000)
    assert bodies[0]["imageData"].startswith("data:image/"), "fallback did not send the image"
    assert float(_cell(page, "rf")) == 0.5


def test_placing_a_line_across_another_does_not_move_it(page, servers, test_image_path):
    """With a line tool armed, clicking where it crosses an existing line places
    the new line only. It used to start a drag of the existing line too (nudging
    it) and the post-drag guard then swallowed the next click."""
    canvas = _load_with_min_max(page, servers["new"], test_image_path)
    _add_marker(page, canvas, "H-Line", 150, 150, "SM")
    before = page.evaluate(_LINES.replace("[l.type, l.text, l.kind]", "[l.type, l.y]"))
    _add_marker(page, canvas, "V-Line", 100, 150, "R1")  # right on the H-line
    after = page.evaluate(_LINES.replace("[l.type, l.text, l.kind]", "[l.type, l.y]"))
    assert after[:3] == before, (before, after)
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 115, "y": 170})  # immediately: must not be swallowed
    assert (
        page.evaluate("() => import('/static/tlc/js/state.js').then(m => m.state.spots.length)")
        == 1
    )


# --- Annotation files and drafts ---------------------------------------------


def _annotate_and_calculate(page, canvas):
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 50})
    _add_marker(page, canvas, "V-Line", 120, 150, text="Lane A", kind="product 1")
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 135, "y": 150})
    canvas.click(position={"x": 135, "y": 100})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr")


def _load_local(page, servers, image_path):
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", image_path)
    page.wait_for_function(_IMAGE_READY)
    page.wait_for_function("() => document.body.dataset.imageIdentified === '1'")
    return page.locator("#image-canvas")


def test_annotations_export_and_reopen(page, servers, test_image_path, tmp_path):
    """Downloaded annotations reopen onto the same image and give the same Rf."""
    canvas = _load_local(page, servers, test_image_path)
    _annotate_and_calculate(page, canvas)
    before = [_cell(page, "rf", row) for row in range(2)]

    with page.expect_download() as download_info:
        page.click("[data-action='saveAnnotations']")
    path = tmp_path / "annotations.json"
    download_info.value.save_as(path)
    data = json.loads(path.read_text())
    assert data["format"] == "tlc-rf-annotations" and data["version"] == 1
    assert len(data["image"]["sha256"]) == 64
    assert [line["type"] for line in data["lines"]] == ["min", "max", "vertical"]
    assert data["lines"][2]["text"] == "Lane A" and data["lines"][2]["kind"] == "product 1"
    assert [spot["source"] for spot in data["spots"]] == ["manual", "manual"]

    page.click("[data-action='resetCanvas']")
    page.set_input_files("#annotations-input", str(path))
    page.wait_for_selector("text=Annotations opened.")
    page.click("button:has-text('Calculate RF')")
    page.wait_for_function("() => document.querySelectorAll('#values-table tbody tr').length === 2")
    assert [_cell(page, "rf", row) for row in range(2)] == before
    assert _cell(page, "lane") == "Lane A"


def test_annotations_for_another_image_are_refused(page, servers, test_image_path, tmp_path):
    canvas = _load_local(page, servers, test_image_path)  # hashed: the check is real
    other = {
        "format": "tlc-rf-annotations",
        "version": 1,
        "image": {"name": "other.jpg", "sha256": "0" * 64},
        "rotation": 0,
        "fineRotation": 0,
        "plateRegion": None,
        "lines": [{"type": "min", "y": 250}],
        "spots": [],
    }
    path = tmp_path / "other.json"
    path.write_text(json.dumps(other))
    page.set_input_files("#annotations-input", str(path))
    page.wait_for_selector('text=belong to a different image ("other.jpg")')
    assert canvas.is_visible()
    assert page.evaluate("() => document.querySelectorAll('#values-table tbody tr').length") == 0


def test_invalid_annotation_file_is_explained(page, servers, test_image_path, tmp_path):
    _load_local(page, servers, test_image_path)
    path = tmp_path / "notes.json"
    path.write_text('{"hello": 1}')
    page.set_input_files("#annotations-input", str(path))
    page.wait_for_selector("text=This is not a TLC annotation file.")


def test_draft_is_offered_after_reload_and_restores(page, servers, test_image_path):
    """Work survives a reload: loading the same image offers the draft; it is
    never restored by itself."""
    canvas = _load_local(page, servers, test_image_path)
    _annotate_and_calculate(page, canvas)
    page.reload()
    canvas = _load_local(page, servers, test_image_path)
    page.wait_for_selector("#draft-banner:not([hidden])")
    assert "2 spots" in page.inner_text("#draft-text")
    assert page.evaluate("() => document.querySelectorAll('#values-table tbody tr').length") == 0
    page.click("[data-action='restoreDraft']")
    page.wait_for_selector("text=Annotations restored.")
    assert page.locator("#draft-banner").is_hidden()
    page.click("button:has-text('Calculate RF')")
    page.wait_for_function("() => document.querySelectorAll('#values-table tbody tr').length === 2")


def test_reset_and_discard_delete_the_draft(page, servers, test_image_path):
    canvas = _load_local(page, servers, test_image_path)
    _annotate_and_calculate(page, canvas)
    page.click("[data-action='resetCanvas']")
    page.reload()
    _load_local(page, servers, test_image_path)  # hashed: a draft would be offered now
    assert page.locator("#draft-banner").is_hidden()

    canvas = page.locator("#image-canvas")
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.reload()
    _load_local(page, servers, test_image_path)
    page.wait_for_selector("#draft-banner:not([hidden])")
    page.click("[data-action='discardDraft']")
    page.reload()
    _load_local(page, servers, test_image_path)
    assert page.locator("#draft-banner").is_hidden()


def test_annotations_reopen_with_rotation(page, servers, blob_image_path, tmp_path):
    """The file carries the rotation; opening it re-applies it before the marks."""
    canvas = _load_local(page, servers, blob_image_path)
    page.select_option("#rotation-preset", "90")
    page.fill("#rotation-fine", "2.5")
    page.dispatch_event("#rotation-fine", "change")
    page.click("button:has-text('Add Spots')")
    canvas.click(position={"x": 150, "y": 150})
    page.click("button:has-text('Min Line')")
    canvas.click(position={"x": 150, "y": 250})
    page.click("button:has-text('Max Line')")
    canvas.click(position={"x": 150, "y": 50})
    page.click("button:has-text('Calculate RF')")
    page.wait_for_selector("#values-table tbody tr")
    with page.expect_download() as download_info:
        page.click("[data-action='saveAnnotations']")
    path = tmp_path / "rotated.json"
    download_info.value.save_as(path)

    page.click("[data-action='resetCanvas']")
    page.select_option("#rotation-preset", "0")
    page.fill("#rotation-fine", "0")
    page.dispatch_event("#rotation-fine", "change")
    page.set_input_files("#annotations-input", str(path))
    page.wait_for_selector("text=Annotations opened.")
    assert page.input_value("#rotation-preset") == "90"
    assert page.input_value("#rotation-fine") == "2.5"

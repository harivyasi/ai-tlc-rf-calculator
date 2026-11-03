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
        rf = float(page.locator("#values-table tbody tr").first.locator("td").nth(2).inner_text())
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
    compound = (
        page.locator("#values-table tbody tr").first.locator("td").nth(1).inner_text().strip()
    )
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
    row = page.locator("#values-table tbody tr").first
    assert row.locator("td").first.inner_text().strip() == "", "unmarked lane should be blank"
    assert row.locator("td").nth(1).inner_text().strip() == "Unknown"


# --- Export buttons: download pair vs ELN-upload pair -----------------------

_EXPORT_VISIBLE = (
    "() => Array.from(document.querySelectorAll('#export-row .lab-btn'))"
    ".filter((b) => b.offsetParent !== null).length"
)


def test_export_local_shows_only_download_pair(page, servers, test_image_path):
    """A local upload shows just the two download buttons (the ELN-upload pair is
    hidden and the download pair expands)."""
    _stub_cdn(page)
    page.goto(servers["new"] + "/")
    page.click("#local-source-btn")
    page.set_input_files("#local-image-input", test_image_path)
    page.wait_for_function(_IMAGE_READY)
    assert page.evaluate(_EXPORT_VISIBLE) == 2
    assert not page.locator("#export-row .export-eln").first.is_visible()


def test_export_eln_shows_all_four(page, servers, test_image_path):
    """An ELN-sourced plate shows all four buttons (download + upload × CSV/Image)."""
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
    assert page.evaluate(_EXPORT_VISIBLE) == 4

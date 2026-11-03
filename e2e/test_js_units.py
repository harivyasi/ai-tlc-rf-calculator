"""Unit tests for the frontend's pure logic — no npm.

The page already loads the ES modules, so each test dynamically ``import()``s a
module in the browser and asserts on its exported pure functions. This gives
real unit coverage of `geometry`, `isHorizontalLine`, and the `textScale` clamp
without adding a Node/JS test toolchain.
"""

from __future__ import annotations


def test_geometry_clamp_and_point_in_region(page, servers):
    page.goto(servers["new"] + "/")
    result = page.evaluate(
        """async () => {
            const g = await import('/static/tlc/js/geometry.js');
            const R = { x: 0, y: 0, width: 10, height: 20 };
            return {
                clamp_below: g.clamp(-3, 0, 10),
                clamp_above: g.clamp(99, 0, 10),
                clamp_inside: g.clamp(4, 0, 10),
                in_true: g.pointInRegion(5, 5, R),
                in_edge: g.pointInRegion(10, 20, R),
                in_false_x: g.pointInRegion(11, 5, R),
                in_false_y: g.pointInRegion(5, 21, R),
            };
        }"""
    )
    assert result == {
        "clamp_below": 0,
        "clamp_above": 10,
        "clamp_inside": 4,
        "in_true": True,
        "in_edge": True,
        "in_false_x": False,
        "in_false_y": False,
    }


def test_is_horizontal_line_by_type(page, servers):
    page.goto(servers["new"] + "/")
    result = page.evaluate(
        """async () => {
            const a = await import('/static/tlc/js/annotations.js');
            return {
                min: a.isHorizontalLine({ type: 'min' }),
                max: a.isHorizontalLine({ type: 'max' }),
                horizontal: a.isHorizontalLine({ type: 'horizontal' }),
                vertical: a.isHorizontalLine({ type: 'vertical' }),
            };
        }"""
    )
    assert result == {"min": True, "max": True, "horizontal": True, "vertical": False}


def test_text_scale_reads_and_clamps(page, servers):
    page.goto(servers["new"] + "/")
    result = page.evaluate(
        """async () => {
            const ui = await import('/static/tlc/js/ui.js');
            const el = document.getElementById('text-scale');
            const read = (v) => { el.value = v; return ui.textScale(); };
            return { def: read('100'), hi: read('9999'), lo: read('1'), bad: read('abc') };
        }"""
    )
    # 100% -> 1.0; clamped to [0.1, 5.0]; non-numeric -> 1.0
    assert result == {"def": 1.0, "hi": 5.0, "lo": 0.1, "bad": 1.0}

"""What is drawn over a model on behalf of a plugin (lines, outlines of elements, element colours,
labels), on whichever viewport is on screen."""

import pytest
from playwright.async_api import Page, async_playwright

from .test_render_backends import (
    NATIVE,
    _assert_drawing_with,
    _count,
    _is_coloured,
    _open_viewer,
    _pixels,
    _show_result,
    _until,
)

# Asks the viewer, before it starts, for the scene verbs plugins draw with (plugins/index.ts).
SCRIPTED_SCENE = "window.__adaScriptedScene = true;"
SCENE = "window.__adaScriptedScene"
# The part of the page the model is drawn in, clear of the panels and the toolbars.
MODEL = (420, 150, 1000, 700)
# Every element of the test models, and then some: labels the model has not got are passed over.
ELEMENTS = "Array.from({length: 4000}, (_, i) => i + 1)"


def _is_magenta(px) -> bool:  # what the tests draw in: no colour map, model or toolbar colour
    return px[0] > 180 and px[1] < 90 and px[2] > 180


async def _magenta(page: Page) -> int:
    return _count((await _pixels(page)).crop(MODEL), _is_magenta)


async def _coloured(page: Page) -> int:
    return _count((await _pixels(page)).crop(MODEL), _is_coloured)


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_plugin_overlays_are_drawn_by_whichever_viewport_is_on_screen(ws_server, backend, fem_files):
    """Lines, the outline of elements, colours painted over elements and labels pinned to them: each
    appears when a plugin asks for it and goes when it is removed."""
    result_file = fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIF"
    async with async_playwright() as playwright:
        browser, page, state = await _open_viewer(playwright, ws_server, backend, SCRIPTED_SCENE)
        try:
            await _show_result(page, ws_server, result_file)
            await _assert_drawing_with(page, backend, state)
            assert await page.evaluate(f"{SCENE}.hasFeaModel()"), f"{backend}: no FEA model for the overlays"
            assert await _magenta(page) == 0

            async def drawn(what: str, at_least: int) -> int:
                async def enough():
                    n = await _magenta(page)
                    return n if n >= at_least else 0

                return await _until(page, enough, f"{backend}: {what} to be drawn")

            async def gone(what: str) -> None:
                async def none():
                    return await _magenta(page) == 0

                await _until(page, none, f"{backend}: {what} to be removed")

            # lines across the model, in its own coordinates, drawn over it
            await page.evaluate(
                f"""window.__lines = {SCENE}.addLines("t", {{
                    segments: [-100, -100, -100, 100, 100, 100], color: "#ff00ff", widthPx: 6, throughModel: true}})"""
            )
            await drawn("a plugin's lines", 300)
            await page.evaluate("window.__lines.remove()")
            await gone("a plugin's lines")

            # the outline of every element: the model's border
            outlined = await page.evaluate(
                f"""(window.__outline = {SCENE}.addElementOutlines("t", {{
                    elements: {ELEMENTS}, color: "#ff00ff", widthPx: 5, throughModel: true}})) !== null"""
            )
            assert outlined, f"{backend}: no outline for the model's elements"
            await drawn("the outline of the elements", 300)
            assert await page.evaluate(f'{SCENE}.addElementOutlines("t", {{elements: [987654]}})') is None
            await page.evaluate("window.__outline.remove()")
            await gone("the outline")

            # colours painted over the elements: the model turns the painted colour
            before = await _coloured(page)
            await page.evaluate(
                f"""window.__paint = {SCENE}.paintFeaElements("t", {{colors: new Map(), neutral: "#ff00ff"}})"""
            )
            painted = await drawn("the painted elements", 5000)
            assert painted > 0.5 * before, f"{backend}: the paint covers {painted} px of a model of {before} px"
            await page.evaluate("window.__paint.remove()")
            await gone("the paint")

            # a label pinned to elements, and one at a point
            await page.evaluate(
                f"""window.__labels = {SCENE}.addLabels("t", [
                    {{atElements: {ELEMENTS}, text: "pinned", background: "#ff00ff", color: "#ff00ff"}},
                ], {{heightPx: 40}})"""
            )
            await drawn("a label", 1500)
            # everything an owner drew goes with the owner
            await page.evaluate(f'{SCENE}.remove("t")')
            await gone("the owner's overlays")

            # elements hidden and shown again through the same verbs
            await page.evaluate(f"{SCENE}.hideFeaElements({ELEMENTS})")

            async def hidden():
                return await _coloured(page) < 0.1 * before

            await _until(page, hidden, f"{backend}: the elements to be hidden")
            assert len(await page.evaluate(f"{SCENE}.hiddenFeaElements()")) > 0
            await page.evaluate(f"{SCENE}.isolateFeaElements(null)")

            async def shown():
                return await _coloured(page) > 0.9 * before

            await _until(page, shown, f"{backend}: the elements to be shown again")
            assert await page.evaluate(f"{SCENE}.hiddenFeaElements()") == []
            await _assert_drawing_with(page, backend, state)
        finally:
            await browser.close()


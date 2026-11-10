"""What is drawn over a model on behalf of a plugin (lines, outlines of elements, element colours,
labels) and the element sets of an FEA result, on whichever viewport is on screen."""

import asyncio
import shutil

import pytest

import ada
from playwright.async_api import Page, async_playwright

from .test_render_backends import (
    NATIVE,
    _assert_drawing_with,
    _command,
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


def _sets(sets: dict[str, range]) -> list[dict]:
    """Named element sets as a result carries them into the viewer."""
    return [
        {"name": name, "members": [f"EL{label}" for label in labels], "fe_object_type": "element"}
        for name, labels in sets.items()
    ]


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_fea_sets_are_listed_and_isolated_on_whichever_viewport_is_on_screen(
    ws_server, backend, fem_files, tmp_path
):
    """The element sets of a result: the Scene tree lists them, isolating one leaves its elements on
    screen, and clearing the isolation brings the rest back."""
    source = fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIF"
    # A copy of its own: what the viewer is sent for a file is kept and reused, without the sets.
    result = ada.from_fem_res(shutil.copy(source, tmp_path / source.name))
    # the first half of the elements, and the rest
    result.sesam_groups = _sets({"lowerhalf": range(1, 181), "upperhalf": range(181, 361)})
    async with async_playwright() as playwright:
        browser, page, state = await _open_viewer(playwright, ws_server, backend)
        try:
            await asyncio.to_thread(lambda: result.show(port=ws_server.port, unique_id=1, ping_timeout=10))

            async def painted():
                return await _coloured(page) > 5000

            await _until(page, painted, f"{backend}: the result field to be painted", seconds=40)
            await _command(page, "Selection tree")

            async def listed():
                return await page.get_by_role("treeitem").filter(has_text="Sets").count() >= 1

            await _until(page, listed, f"{backend}: the Scene tree to list the result's sets")
            whole = await _coloured(page)

            # isolate one set by name: about half of the result stays
            await _command(page, "Isolate FEA sets")
            await page.keyboard.type("lowerhalf")
            await page.keyboard.press("Enter")

            async def isolated():
                n = await _coloured(page)
                return 0.2 * whole < n < 0.8 * whole

            await _until(page, isolated, f"{backend}: one set to be isolated")
            await _assert_drawing_with(page, backend, state)

            await _command(page, "Clear FEA isolation")

            async def cleared():
                return await _coloured(page) > 0.95 * whole

            await _until(page, cleared, f"{backend}: the isolation to be cleared")
        finally:
            await browser.close()


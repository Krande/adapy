"""Undo and redo on whichever viewport is on screen: the three.js one and the three.js-free one answer
the same commands and keys through one history."""

import asyncio

import pytest
from playwright.async_api import async_playwright

from .test_render_backends import (
    CENTRE,
    NATIVE,
    _click,
    _command,
    _count,
    _is_model,
    _is_selected,
    _model_on_screen,
    _open_viewer,
    _pixels,
    _row_of_boxes,
    _until,
)


async def _picture(page) -> tuple[int, int]:
    """What the view shows, as two numbers: the pixels of selected objects and of unselected ones."""
    image = (await _pixels(page)).crop((300, 130, 1100, 700))
    return _count(image, _is_selected), _count(image, _is_model)


def _same(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return all(abs(x - y) <= 0.05 * max(x, y) + 30 for x, y in zip(a, b))


async def _edit_command(page, label: str) -> None:
    """Run the Edit menu's command called exactly `label` from the palette (other commands share the
    word, so the row is clicked rather than the first match taken)."""
    await page.keyboard.press("Control+k")
    await page.wait_for_timeout(300)
    await page.keyboard.type(label)
    await page.wait_for_timeout(400)
    rows = page.get_by_role("option").filter(has=page.get_by_text(label, exact=True))
    row = rows.filter(has=page.get_by_text("Edit", exact=True))
    listed = await page.get_by_role("option").all_inner_texts()
    assert await row.count() == 1, f"the palette lists {listed} for {label!r}"
    assert (
        await row.get_attribute("aria-disabled") != "true"
    ), f"{label} is offered disabled: {await row.get_attribute('title')}"
    await row.click()
    await page.wait_for_timeout(700)


async def _show_row_of_boxes(page, ws_server) -> None:
    await asyncio.to_thread(
        lambda: _row_of_boxes().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
    )
    await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
    await page.wait_for_timeout(500)


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_undo_and_redo_restore_each_picture(ws_server, backend):
    """Select, hide the others, show all, hide the selection: four steps. Undo takes the picture back
    through each of them to the model as loaded, and redo forward again, by key and from the palette."""
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await _show_row_of_boxes(page, ws_server)
            if backend == NATIVE:
                assert await page.evaluate("document.documentElement.dataset.nativeViewport") == "active"

            pictures = [await _picture(page)]

            async def step(act, what: str) -> None:
                await act()
                await page.wait_for_timeout(600)
                pictures.append(await _picture(page))
                assert not _same(pictures[-1], pictures[-2]), f"{backend}: {what} changed nothing ({pictures})"

            await step(lambda: _click(page, *CENTRE), "the click")
            assert pictures[1][0] > 3000, f"{backend}: the click selected nothing"
            await step(lambda: _command(page, "Hide others"), "Hide others")
            assert pictures[2][1] < 0.6 * pictures[1][1], f"{backend}: the other objects are still drawn"
            await step(lambda: _command(page, "Show all"), "Show all")
            await step(lambda: _command(page, "Hide selected"), "Hide selected")
            assert pictures[4][0] < 50, f"{backend}: the hidden object is still highlighted"

            async def expect(index: int, after: str) -> None:
                await page.wait_for_timeout(700)
                got = await _picture(page)
                assert _same(got, pictures[index]), f"{backend}: after {after} the view is {got}, not {pictures[index]}"

            # back to the model as loaded: the key, then the palette's command
            await page.mouse.move(*CENTRE)
            await page.keyboard.press("Control+z")
            await expect(3, "the first undo")
            await page.keyboard.press("Control+z")
            await expect(2, "the second undo")
            await _edit_command(page, "Undo")
            await expect(1, "the third undo")
            await page.keyboard.press("Control+z")
            await expect(0, "the fourth undo")

            # and forward again
            await page.keyboard.press("Control+y")
            await expect(1, "the first redo")
            await _edit_command(page, "Redo")
            await expect(2, "the second redo")
            await page.keyboard.press("Control+Shift+z")
            await expect(3, "the third redo")
            await page.keyboard.press("Control+y")
            await expect(4, "the fourth redo")

            # a new action after an undo: what was undone can no longer be redone
            await page.keyboard.press("Control+z")
            await expect(3, "an undo")
            await _command(page, "Clear selection")
            cleared = await _picture(page)
            assert cleared[0] < 50, f"{backend}: the selection was not cleared"
            await page.keyboard.press("Control+y")
            await page.wait_for_timeout(700)
            assert _same(await _picture(page), cleared), f"{backend}: a redo after a new action changed the view"
        finally:
            await browser.close()


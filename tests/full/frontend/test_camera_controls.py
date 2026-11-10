"""The camera input mapping, end to end: a stored mapping decides what a drag does, on the three.js
viewport and on the three.js-free one."""

import asyncio
import json

import pytest
from PIL import ImageChops
from playwright.async_api import Page, async_playwright

from .test_render_backends import (
    CENTRE,
    NATIVE,
    _model,
    _model_on_screen,
    _open_viewer,
    _pixels,
    _until,
)

# Left drags do nothing, right drags orbit: the opposite of the default, where left orbits and right pans.
RIGHT_ORBITS = {
    "drag": {
        "left": {"none": "none", "shift": "none", "ctrl": "none", "alt": "none"},
        "middle": {"none": "pan", "shift": "pan", "ctrl": "pan", "alt": "pan"},
        "right": {"none": "orbit", "shift": "pan", "ctrl": "zoom", "alt": "orbit"},
    },
    "wheel": {"zoom": False, "inverted": False, "toCursor": False},
    "touch": {"one": "orbit", "two": "zoom-pan"},
    "orbitSpeed": 1,
    "zoomSpeed": 1,
    "damping": False,
}


async def _drag(page: Page, button: str) -> None:
    x, y = CENTRE
    await page.mouse.move(x, y)
    await page.mouse.down(button=button)
    await page.mouse.move(x + 160, y + 40, steps=8)
    await page.mouse.up(button=button)
    await page.wait_for_timeout(800)


# A camera move repaints the model; a stray pixel in an overlay is not one.
MOVED_PIXELS = 2000


async def _changed(page: Page, before) -> bool:
    difference = ImageChops.difference(await _pixels(page), before).convert("L")
    return sum(difference.histogram()[1:]) > MOVED_PIXELS


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_a_stored_mapping_decides_what_a_drag_does(ws_server, backend):
    stored = json.dumps({"state": {"mapping": RIGHT_ORBITS}, "version": 1})
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(
            playwright,
            ws_server,
            backend,
            init_script=f"localStorage.setItem('ada-camera-controls', JSON.stringify({stored}))",
        )
        try:
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            await page.wait_for_timeout(500)

            # The wheel is switched off: the picture stays as it is.
            await page.mouse.move(80, 700)  # over the background, so no hover effect changes the picture
            await page.wait_for_timeout(500)
            still = await _pixels(page)
            await page.mouse.wheel(0, -400)
            await page.wait_for_timeout(600)
            assert not await _changed(page, still), f"{backend}: the wheel zoomed although it is switched off"

            # Left is mapped to nothing: a left drag over the background moves nothing.
            await page.mouse.down()
            await page.mouse.move(240, 740, steps=8)
            await page.mouse.up()
            await page.wait_for_timeout(600)
            assert not await _changed(
                page, still
            ), f"{backend}: a left drag moved the camera although it is mapped to nothing"

            # Right is mapped to orbit: the view turns.
            await _drag(page, "right")
            assert await _changed(page, still), f"{backend}: a right drag did not move the camera"
        finally:
            await browser.close()

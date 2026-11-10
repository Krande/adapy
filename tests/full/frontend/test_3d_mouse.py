"""A 3D mouse moves the camera of whichever viewport is on screen. The device is a pad the browser
lists over the Gamepad API; the page is given one whose cap is held twisted."""

import asyncio
import json

import pytest
from playwright.async_api import async_playwright

from .test_render_backends import (
    NATIVE,
    _model,
    _model_on_screen,
    _open_viewer,
    _pixels,
    _until,
)


async def _view(page) -> bytes:
    """The middle of the viewport, where the model is: clear of the toolbars and status chips."""
    return (await _pixels(page)).crop((340, 150, 940, 650)).tobytes()


# A pad with the vendor's id and six axes, the last (the twist about the vertical) held half way; and
# 3D-mouse support switched on, as the Options panel stores it.
PAD = """
const pad = {
    id: "SpaceMouse Compact (Vendor: 256f Product: c635)", index: 0, connected: true, mapping: "",
    timestamp: 0, axes: [0, 0, 0, 0, 0, 0], buttons: [{pressed: false, value: 0}, {pressed: false, value: 0}],
};
window.__pad = pad;
navigator.getGamepads = () => [pad];
// The device drives a page only while it has focus, which a headless page does not report.
document.hasFocus = () => true;
localStorage.setItem("ada-spacemouse", JSON.stringify(%s));
"""
SETTINGS = {"state": {"enabled": True, "chosen": True}, "version": 0}


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_a_3d_mouse_turns_the_view_of_whichever_viewport_is_on_screen(ws_server, backend):
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend, PAD % json.dumps(SETTINGS))
        try:
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            await page.wait_for_timeout(800)
            still = await _view(page)
            await page.wait_for_timeout(600)
            assert await _view(page) == still, f"{backend}: the view moves with the cap at rest"

            # twist the cap: the view turns, and keeps turning while it is held
            await page.evaluate("window.__pad.axes = [0, 0, 0, 0, 0, 0.5]; window.__pad.timestamp = performance.now()")
            await page.wait_for_timeout(700)
            turned = await _view(page)
            assert turned != still, f"{backend}: twisting the cap did not turn the view"
            await page.wait_for_timeout(500)
            assert await _view(page) != turned, f"{backend}: the view stopped while the cap was held"

            # release it: the view comes to rest
            await page.evaluate("window.__pad.axes = [0, 0, 0, 0, 0, 0]; window.__pad.timestamp = performance.now()")
            await page.wait_for_timeout(1500)
            rest = await _view(page)
            await page.wait_for_timeout(500)
            assert await _view(page) == rest, f"{backend}: the view kept moving after the cap was released"
        finally:
            await browser.close()

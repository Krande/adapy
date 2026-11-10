"""The viewer's everyday scenarios on each render backend: three.js (WebGL), the adacpp WebGPU renderer,
and adacpp alone with no WebGL renderer constructed.

Every leg first checks WHICH renderer is drawing (the viewer publishes it on the document element), so a
WebGPU leg cannot pass by silently falling back to three.js. What is asserted is what a user sees: pixels
of the page, not internals. Chromium's software adapter is enough for WebGPU; a browser that offers no
adapter at all skips the WebGPU legs with that reason.
"""

import asyncio
import io
import json

import pytest
from PIL import Image
from playwright.async_api import Page, async_playwright

import ada
from ada.comms.web.serve import start_serving
from ada.comms.wsock.client_async import WebSocketClientAsync

# Headless Chromium keeps a WebGPU device alive next to WebGL canvases only on its Vulkan (SwiftShader) path.
CHROMIUM_ARGS = ["--enable-unsafe-webgpu", "--enable-features=Vulkan", "--use-angle=swiftshader"]
VIEWPORT = {"width": 1280, "height": 800}
CENTRE = (VIEWPORT["width"] // 2, VIEWPORT["height"] // 2)

# renderer query value, stored performance switches, what the viewer must report it draws with
BACKENDS = {
    "three": ("three", {}, "off"),
    "adacpp": ("adacpp", {}, "active"),
    # everything three.js still did handed over, and no WebGL context at all
    "adacpp-only": ("adacpp", {"adacppNoWebGL": True}, "active"),
}

COUNT_WEBGL = """
window.__webglContexts = 0;
const getContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    if (String(kind).startsWith("webgl")) window.__webglContexts++;
    return getContext.call(this, kind, ...rest);
};
"""

HAS_ADAPTER = "async () => !!navigator.gpu && !!(await navigator.gpu.requestAdapter())"


def _model() -> ada.Assembly:
    p = ada.Part("demo")
    p.add_plate(ada.Plate("deck", [(0, 0), (4, 0), (4, 2), (0, 2)], 0.02))
    p.add_shape(ada.PrimBox("box", (1.5, 0.5, 0.02), (2.5, 1.5, 1.0)))
    return ada.Assembly("a") / p


async def _pixels(page: Page) -> Image.Image:
    return Image.open(io.BytesIO(await page.screenshot())).convert("RGB")


def _count(image: Image.Image, predicate) -> int:
    # By colour, not by pixel: a page has a few hundred distinct colours and a million pixels.
    return sum(n for n, colour in image.getcolors(maxcolors=image.width * image.height) if predicate(colour))


def _is_selected(px) -> bool:  # the selection colour: pure blue (the toolbar's blue has green in it)
    return px[2] > 200 and px[0] < 45 and px[1] < 45


def _is_background(px) -> bool:  # the viewport's dark grey
    return max(px) < 80 and max(px) - min(px) < 12


def _is_model(px) -> bool:  # the default light grey of an unpainted model, against the dark background
    return min(px) > 150 and max(px) - min(px) < 25


def _is_coloured(px) -> bool:  # a result colour map: clearly not grey
    return max(px) - min(px) > 70


async def _viewport_ready(page: Page) -> bool:
    return _count(await _pixels(page), _is_background) > 500000


async def _model_on_screen(page: Page) -> bool:
    """The model against the viewport's background: not the blank page before the viewer has started."""
    image = await _pixels(page)
    return _count(image, _is_model) > 20000 and _count(image, _is_background) > 200000


async def _command(page: Page, label: str) -> None:
    """Run a command the way a user does: the palette (Ctrl+K), its label, Enter."""
    await page.keyboard.press("Control+k")
    await page.wait_for_timeout(300)
    await page.keyboard.type(label)
    await page.wait_for_timeout(400)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(700)


async def _click(page: Page, x: int, y: int) -> None:
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.up()
    await page.wait_for_timeout(1000)


async def _until(page: Page, check, what: str, seconds: float = 20.0):
    for _ in range(int(seconds / 0.25)):
        value = await check()
        if value:
            return value
        await page.wait_for_timeout(250)
    raise AssertionError(f"timed out waiting for {what}")


async def _open_viewer(playwright, ws_server, backend: str):
    """A viewer page on `backend`, connected to the websocket server; skips when WebGPU has no adapter here."""
    query, perf, state = BACKENDS[backend]
    browser = await playwright.chromium.launch(headless=True, args=CHROMIUM_ARGS)
    page = await browser.new_page(viewport=VIEWPORT)
    await page.add_init_script(COUNT_WEBGL)
    if perf:
        stored = json.dumps({"state": perf, "version": 1})
        await page.add_init_script(f"localStorage.setItem('ada-perf', JSON.stringify({stored}))")
    server, _ = start_serving(web_port=0, ws_port=ws_server.port, unique_id=1, non_blocking=True)
    await page.goto(f"http://localhost:{server.server_address[1]}/?renderer={query}", timeout=20000)
    if query == "adacpp" and not await page.evaluate(HAS_ADAPTER):
        await browser.close()
        pytest.skip("this Chromium offers no WebGPU adapter")
    async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        for _ in range(100):
            if await ws_client.check_target_liveness(target_id=1):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("the viewer never became live on the websocket")
    # Live on the socket is not yet ready for a scene: the viewport (grid, gizmo) has to be up, or the
    # first model sent is dropped.
    await _until(page, lambda: _viewport_ready(page), "the empty viewport")
    return browser, page, state


async def _assert_backend(page: Page, backend: str, state: str) -> None:
    async def settled():
        got = await page.evaluate("document.documentElement.dataset.adacppRenderer")
        return got if got in ("off", "active", "failed") else None

    got = await _until(page, settled, "the renderer to settle")
    reason = await page.evaluate("document.documentElement.dataset.adacppRendererError || ''")
    assert got == state, f"{backend}: the viewer reports renderer state {got!r} ({reason}), expected {state!r}"
    if backend == "adacpp-only":
        assert await page.evaluate("window.__webglContexts") == 0, "WebGPU-only mode created a WebGL context"


@pytest.mark.parametrize("backend", list(BACKENDS))
async def test_load_select_hide_section_and_pick(ws_server, backend):
    async with async_playwright() as playwright:
        browser, page, state = await _open_viewer(playwright, ws_server, backend)
        try:
            # Off the event loop: show() talks to the websocket server synchronously, and the page needs
            # the loop to answer it.
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )

            # load: the model is on screen, drawn by the backend asked for
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            await _assert_backend(page, backend, state)
            loaded = _count(await _pixels(page), _is_model)

            # pick + select: a click on the box in the middle of the view highlights it
            await _click(page, *CENTRE)
            selected = _count(await _pixels(page), _is_selected)
            assert selected > 3000, f"{backend}: clicking the box selected nothing ({selected} highlighted pixels)"

            # hide: the selection goes, and with it the highlight
            await _command(page, "Hide selected")
            image = await _pixels(page)
            assert _count(image, _is_selected) < 50, f"{backend}: the hidden box is still highlighted"

            await _command(page, "Show all")
            restored = _count(await _pixels(page), _is_model)
            assert abs(restored - loaded) < 0.05 * loaded, f"{backend}: Show all did not bring the model back"

            # section: a plane through the model removes about half of it from view
            await _command(page, "Section plane X")
            cut = _count(await _pixels(page), _is_model)
            assert (
                0.2 * loaded < cut < 0.85 * loaded
            ), f"{backend}: the section plane cut {loaded} -> {cut} model pixels"
            await _command(page, "Remove section planes")

            # a click on the background selects nothing
            await _click(page, 60, VIEWPORT["height"] - 60)
            assert _count(await _pixels(page), _is_selected) < 50
            await _assert_backend(page, backend, state)  # still the same renderer after all of that
        finally:
            await browser.close()


@pytest.mark.parametrize("backend", list(BACKENDS))
async def test_fea_field_and_warp_playback(ws_server, backend, fem_files):
    result_file = fem_files / "cantilever/calculix/eigen_shell_cantilever_calculix.frd"
    async with async_playwright() as playwright:
        browser, page, state = await _open_viewer(playwright, ws_server, backend)
        try:
            result = ada.from_fem_res(result_file)
            await asyncio.to_thread(lambda: result.show(port=ws_server.port, unique_id=1, ping_timeout=10))

            # field: the result is painted with a colour map
            async def coloured():
                return _count(await _pixels(page), _is_coloured) > 5000

            await _until(page, coloured, "the result field to be painted", seconds=40)
            await _assert_backend(page, backend, state)

            # warp playback: the mode shape moves, so frames differ while it plays
            still = (await _pixels(page)).tobytes()
            await _command(page, "Play / pause results")
            frames = set()
            for _ in range(8):
                await page.wait_for_timeout(250)
                frames.add((await _pixels(page)).tobytes())
            assert len(frames) > 2, f"{backend}: the warp did not animate ({len(frames)} distinct frames)"
            await _command(page, "Play / pause results")
            assert still  # the still frame was captured before playback started
            await _assert_backend(page, backend, state)
        finally:
            await browser.close()

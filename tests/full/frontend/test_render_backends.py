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

# WebGPU in headless Chromium, with or without a GPU in the machine: Vulkan on the bundled software
# implementation (SwiftShader). Without `--use-vulkan=swiftshader` a machine with no GPU (a CI runner)
# still offers an adapter, but the device is destroyed right after its first frame.
CHROMIUM_ARGS = [
    "--enable-unsafe-webgpu",
    "--enable-features=Vulkan",
    "--use-angle=swiftshader",
    "--use-vulkan=swiftshader",
]
VIEWPORT = {"width": 1280, "height": 800}
CENTRE = (VIEWPORT["width"] // 2, VIEWPORT["height"] // 2)

# renderer query value, stored performance switches, what the viewer must report it draws with
BACKENDS = {
    "three": ("three", {}, "off"),
    "adacpp": ("adacpp", {}, "active"),
    # everything three.js still did handed over, and no WebGL context at all
    "adacpp-only": ("adacpp", {"adacppNoWebGL": True}, "active"),
}
# The three.js-free viewport is a different viewport, with its own state and its own small UI.
NATIVE = "native"

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


async def _open_viewer(playwright, ws_server, backend: str, init_script: str | None = None):
    """A viewer page on `backend`, connected to the websocket server; skips when WebGPU has no adapter here.
    `init_script` runs in the page before the viewer starts (stored settings)."""
    query, perf, state = BACKENDS.get(backend, (backend, {}, "off"))
    browser = await playwright.chromium.launch(headless=True, args=CHROMIUM_ARGS)
    page = await browser.new_page(viewport=VIEWPORT)
    await page.add_init_script(COUNT_WEBGL)
    if init_script:
        await page.add_init_script(init_script)
    if perf:
        stored = json.dumps({"state": perf, "version": 1})
        await page.add_init_script(f"localStorage.setItem('ada-perf', JSON.stringify({stored}))")
    server, _ = start_serving(web_port=0, ws_port=ws_server.port, unique_id=1, non_blocking=True)
    await page.goto(f"http://localhost:{server.server_address[1]}/?renderer={query}", timeout=20000)
    if query != "three" and not await page.evaluate(HAS_ADAPTER):
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


async def _assert_drawing_with(page: Page, backend: str, state: str) -> None:
    """What draws is what was asked for: the renderer's state, or the three.js-free viewport up and no
    WebGL context made."""
    if backend != NATIVE:
        return await _assert_backend(page, backend, state)
    got = await page.evaluate("document.documentElement.dataset.nativeViewport")
    assert got == "active", f"the three.js-free viewport is {got!r}, not drawing"
    assert await page.evaluate("window.__webglContexts") == 0, "the three.js-free viewport created a WebGL context"


@pytest.mark.parametrize("backend", [*BACKENDS, NATIVE])
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
            await _assert_drawing_with(page, backend, state)

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

            # step: stopped, the shape is undeformed; the next mode is another picture
            await _command(page, "Stop results")
            stopped = (await _pixels(page)).tobytes()
            await _command(page, "Next result step")

            async def stepped():
                return (await _pixels(page)).tobytes() != stopped

            await _until(page, stepped, f"{backend}: the next result step to be drawn")

            # legend: the scale the colours are read against is on screen
            assert await page.get_by_text("Range: shown elements").count() >= 1, f"{backend}: no colour legend"
            await _assert_drawing_with(page, backend, state)
        finally:
            await browser.close()


async def test_three_free_viewport_draws_without_webgl(ws_server):
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, NATIVE)
        try:
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            assert await page.evaluate("document.documentElement.dataset.nativeViewport") == "active"
            assert (
                await page.evaluate("window.__webglContexts") == 0
            ), "the three.js-free viewport created a WebGL context"
            await _click(page, *CENTRE)
            assert _count(await _pixels(page), _is_selected) > 3000
        finally:
            await browser.close()


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_scene_tree_and_object_info_follow_whichever_viewport_is_on_screen(ws_server, backend):
    """The viewer's own Scene tree and object info, not a second set for the three.js-free viewport:
    the tree lists the model, a click in 3D selects its row and names the object, and a click on a row
    selects in 3D."""
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            await _command(page, "Selection tree")
            rows = page.get_by_role("treeitem")

            async def listed():
                return await rows.count() >= 1

            await _until(page, listed, "the Scene tree to list the model")

            # 3D -> tree and info: a click on the box selects its row
            await _click(page, *CENTRE)
            assert _count(await _pixels(page), _is_selected) > 3000, f"{backend}: the click selected nothing"

            async def box_row_selected():
                selected = page.locator("[role=treeitem][aria-selected=true]")
                return await selected.count() == 1 and "box" in (await selected.first.inner_text())

            await _until(page, box_row_selected, f"{backend}: the box's row to be selected in the tree")

            # tree -> 3D: the deck's row selects the deck, which is larger on screen than the box
            box_pixels = _count(await _pixels(page), _is_selected)
            await rows.filter(has_text="deck").first.click()
            await page.wait_for_timeout(1000)
            deck_pixels = _count(await _pixels(page), _is_selected)
            assert (
                deck_pixels > 3000 and deck_pixels != box_pixels
            ), f"{backend}: clicking the deck's row left {deck_pixels} selected pixels (box: {box_pixels})"
        finally:
            await browser.close()


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_commands_and_keys_reach_whichever_viewport_is_on_screen(ws_server, backend):
    """Hide, show all and the camera, from the palette and from the keyboard: one set of commands and
    keys, answered by the three.js viewport and by the three.js-free one."""
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            if backend == NATIVE:
                assert await page.evaluate("document.documentElement.dataset.nativeViewport") == "active"
            loaded = _count(await _pixels(page), _is_model)

            await _click(page, *CENTRE)
            assert _count(await _pixels(page), _is_selected) > 3000, f"{backend}: the click selected nothing"

            # palette: hide the selection, then bring everything back
            await _command(page, "Hide selected")
            image = await _pixels(page)
            assert _count(image, _is_selected) < 50, f"{backend}: the hidden box is still highlighted"
            hidden = _count(image, _is_model)
            await _command(page, "Show all")
            assert _count(await _pixels(page), _is_model) > hidden, f"{backend}: Show all brought nothing back"

            # keyboard: the same two, by their keys
            await _click(page, *CENTRE)
            assert _count(await _pixels(page), _is_selected) > 3000
            await page.keyboard.press("Shift+H")
            await page.wait_for_timeout(700)
            assert _count(await _pixels(page), _is_selected) < 50, f"{backend}: Shift+H hid nothing"
            await page.keyboard.press("Shift+U")
            await page.wait_for_timeout(700)

            # fit selection: the selected box fills more of the view than it did
            await _click(page, *CENTRE)
            before = _count(await _pixels(page), _is_selected)
            assert before > 3000
            await _command(page, "Fit selection")
            await page.wait_for_timeout(1200)
            framed = _count(await _pixels(page), _is_selected)
            assert framed > 1.5 * before, f"{backend}: Fit selection took the box from {before} to {framed} pixels"
            await _command(page, "Clear selection")

            # section: a plane through the model removes part of it from view; removing it restores it
            await _command(page, "Fit all")
            await page.wait_for_timeout(1200)
            whole = _count(await _pixels(page), _is_model)
            await _command(page, "Section plane X")
            cut = _count(await _pixels(page), _is_model)
            assert 0.2 * whole < cut < 0.85 * whole, f"{backend}: the section plane cut {whole} -> {cut} model pixels"
            if backend == NATIVE:
                # the plane's handle: its arrow starts at the model's centre, which Fit all put mid-screen;
                # dragging it along the arrow moves the cut
                await page.mouse.move(*CENTRE)
                await page.mouse.down()
                await page.mouse.move(CENTRE[0] + 40, CENTRE[1] - 20, steps=4)
                await page.mouse.move(CENTRE[0] + 80, CENTRE[1] - 40, steps=4)
                await page.mouse.up()
                await page.wait_for_timeout(700)
                moved = _count(await _pixels(page), _is_model)
                assert abs(moved - cut) > 0.05 * cut, f"dragging the plane's arrow left the cut at {cut} -> {moved}"
                assert _count(await _pixels(page), _is_selected) < 50, "the drag on the arrow selected something"
            await _command(page, "Remove section planes")
            restored = _count(await _pixels(page), _is_model)
            assert abs(restored - whole) < 0.05 * whole, f"{backend}: removing the plane left {restored} of {whole}"

            # camera: a named view and a key step each change the picture; fit all brings the model back
            async def changes(act, what: str) -> None:
                before = (await _pixels(page)).tobytes()
                await act()
                await page.wait_for_timeout(1200)
                assert (await _pixels(page)).tobytes() != before, f"{backend}: {what} changed nothing"

            await changes(lambda: _command(page, "Top view"), "the top view")
            await changes(lambda: page.keyboard.press("Control+ArrowLeft"), "Ctrl+Left (orbit)")
            await changes(lambda: page.keyboard.press("Control+Shift+ArrowUp"), "Ctrl+Shift+Up (pan)")
            await changes(lambda: page.keyboard.press("Alt+3"), "Alt+3 (the front view)")
            if backend == NATIVE:
                # the orientation gizmo in the corner: an axis end looks at the model from there
                await changes(
                    lambda: page.get_by_role("button", name="View from Z", exact=True).click(),
                    "the orientation gizmo's Z",
                )
            await _command(page, "Fit all")
            await page.wait_for_timeout(1200)
            fitted = _count(await _pixels(page), _is_model)
            assert fitted > 0.2 * loaded, f"{backend}: Fit all left {fitted} model pixels of {loaded}"
        finally:
            await browser.close()


def _is_label(px) -> bool:  # a name label's amber text and border: no model, selection or toolbar colour
    return px[0] > 200 and px[1] > 150 and px[2] < 170 and px[0] - px[2] > 70


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_name_labels_are_drawn_by_whichever_viewport_is_on_screen(ws_server, backend):
    """One switch, "Object names", labels the selected object on the three.js viewport and on the
    three.js-free one: the label appears over the selection, follows it, and goes when switched off."""
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            if backend == NATIVE:
                assert await page.evaluate("document.documentElement.dataset.nativeViewport") == "active"

            await _click(page, *CENTRE)
            assert _count(await _pixels(page), _is_selected) > 3000, f"{backend}: the click selected nothing"
            unlabelled = _count(await _pixels(page), _is_label)
            assert unlabelled < 10, f"{backend}: {unlabelled} label-coloured pixels before any label"

            await _command(page, "Object names")
            labelled = _count(await _pixels(page), _is_label)
            assert labelled > 30, f"{backend}: the selected box got no label ({labelled} label pixels)"

            # the label is of the selection: with nothing selected there is none
            await _command(page, "Clear selection")
            assert _count(await _pixels(page), _is_label) < 10, f"{backend}: the label outlived the selection"
            await _click(page, *CENTRE)
            again = _count(await _pixels(page), _is_label)
            assert again > 30, f"{backend}: selecting again brought no label back ({again} label pixels)"

            await _command(page, "Object names")
            left = _count(await _pixels(page), _is_label)
            assert left < 10, f"{backend}: switching the labels off left {left} label pixels"
        finally:
            await browser.close()


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_take_screenshot_saves_the_view_of_whichever_viewport_is_on_screen(ws_server, backend):
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            async with page.expect_download(timeout=20000) as download:
                await _command(page, "Take screenshot")
            saved = await (await download.value).path()
            picture = Image.open(saved).convert("RGB")
            assert picture.size == (1920, 1080)
            assert _count(picture, _is_model) > 20000, f"{backend}: the screenshot does not show the model"
        finally:
            await browser.close()


def _second_model_message(instance_id: int, tmp_path) -> bytes:
    """A websocket message that adds another model beside the one on screen."""
    from ada.comms.fb_wrap_model_gen import (
        CommandTypeDC,
        FileObjectDC,
        FilePurposeDC,
        FileTypeDC,
        MessageDC,
        SceneDC,
        SceneOperationsDC,
        TargetTypeDC,
    )
    from ada.comms.fb_wrap_serializer import serialize_root_message

    part = ada.Part("annex")
    part.add_shape(ada.PrimBox("tower", (1.5, 2.5, 0.0), (2.5, 3.5, 2.0)))
    glb = tmp_path / "annex.glb"
    (ada.Assembly("b") / part).to_gltf(glb)
    file_object = FileObjectDC(
        name="annex.glb", file_type=FileTypeDC.GLB, purpose=FilePurposeDC.DESIGN, filedata=glb.read_bytes()
    )
    return serialize_root_message(
        MessageDC(
            instance_id=instance_id,
            command_type=CommandTypeDC.UPDATE_SCENE,
            target_group=TargetTypeDC.WEB,
            target_id=1,
            scene=SceneDC(operation=SceneOperationsDC.ADD, current_file=file_object),
        )
    )


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_a_second_model_joins_the_first_on_whichever_viewport_is_on_screen(ws_server, backend, tmp_path):
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await asyncio.to_thread(
                lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            await _command(page, "Selection tree")
            roots = page.locator("[role=treeitem][aria-level='1']")

            async def one_root():
                return await roots.count() == 1

            await _until(page, one_root, "the Scene tree to list the first model")
            first = _count(await _pixels(page), _is_model)

            async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as client:
                await client.websocket.send(_second_model_message(client.instance_id, tmp_path))

                async def two_roots():
                    return await roots.count() == 2

                await _until(page, two_roots, f"{backend}: the second model's root in the Scene tree")
            await _command(page, "Fit all")
            await page.wait_for_timeout(1500)
            both = _count(await _pixels(page), _is_model)
            assert (
                both > 20000
            ), f"{backend}: the models are not on screen together ({both} model pixels, {first} before)"

            # the second model's root row selects what that model draws, by its own model's key
            await roots.nth(1).click()
            await page.wait_for_timeout(1000)
            assert (
                _count(await _pixels(page), _is_selected) > 1500
            ), f"{backend}: the second model's row selected nothing"
        finally:
            await browser.close()


def _row_of_boxes() -> ada.Assembly:
    """Three unit boxes in a row along x, a box's width apart: the middle one is at the model's centre."""
    p = ada.Part("row")
    for i, name in enumerate(("left", "middle", "right")):
        p.add_shape(ada.PrimBox(name, (2 * i, 0, 0), (2 * i + 1, 1, 1)))
    return ada.Assembly("a") / p


def _is_box_edge(px) -> bool:  # a selection box's default light blue: lighter than the selection's blue
    return 30 < px[0] < 110 and 140 < px[1] < 205 and px[2] > 220


def _is_x_handle(px) -> bool:  # the red of a selection box's two x handles (the move arrows are a purer red)
    return 190 < px[0] < 250 and 50 < px[1] < 110 and 55 < px[2] < 115


def _box_edges(image: Image.Image) -> int:
    """Box-coloured pixels over the model: the toolbar and the orientation gizmo in the corner, which has
    a blue of its own, are left out."""
    return _count(image.crop((0, 130, 1100, 660)), _is_box_edge)


def _x_handles(image: Image.Image) -> list[tuple[int, int]]:
    """Where the red handles are in the middle of the view, left to right: the centre of each group of
    handle-coloured pixels (a few stray pixels of another colour's edge are no group)."""
    points = sorted((x, y) for y in range(130, 700) for x in range(100, 1100) if _is_x_handle(image.getpixel((x, y))))
    groups: list[list[tuple[int, int]]] = []
    for point in points:
        if groups and point[0] - groups[-1][-1][0] <= 6:
            groups[-1].append(point)
        else:
            groups.append([point])
    return [(sum(x for x, _ in g) // len(g), sum(y for _, y in g) // len(g)) for g in groups if len(g) >= 40]


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_box_selection_on_whichever_viewport_is_on_screen(ws_server, backend):
    """A selection box, on the three.js viewport and on the three.js-free one: made around the selected
    object from the palette, it is drawn and selects what it holds; dragging a face's handle outwards
    takes in the neighbour; its right-click menu switches it to "fully within", which lets the neighbour
    go again; and a rectangle dragged with Shift selects everything inside it."""
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await asyncio.to_thread(
                lambda: _row_of_boxes().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10)
            )
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            if backend == NATIVE:
                assert await page.evaluate("document.documentElement.dataset.nativeViewport") == "active"

            await _click(page, *CENTRE)
            one = _count(await _pixels(page), _is_selected)
            assert one > 3000, f"{backend}: the click selected nothing"

            # a box around the middle object: drawn, and selecting just that object (its neighbours are
            # a box's width away)
            await _command(page, "Box around selection")
            image = await _pixels(page)
            edges = _box_edges(image)
            assert edges > 100, f"{backend}: no selection box is drawn ({edges} box-coloured pixels)"
            boxed = _count(image, _is_selected)
            assert 0.6 * one < boxed < 1.3 * one, f"{backend}: the box selects {boxed} pixels, the object {one}"

            # resize: the outer x handle, dragged away from the other one, reaches a neighbour
            handles = _x_handles(image)
            assert len(handles) >= 2, f"{backend}: the box's x handles are not on screen ({handles})"
            (ax, ay), (bx, by) = handles[0], handles[-1]
            await page.mouse.move(bx, by)
            await page.mouse.down()
            for step in range(1, 9):
                await page.mouse.move(bx + (bx - ax) * 1.3 * step / 8, by + (by - ay) * 1.3 * step / 8)
                await page.wait_for_timeout(40)
            await page.mouse.up()
            await page.wait_for_timeout(800)
            enlarged = _count(await _pixels(page), _is_selected)
            assert enlarged > 1.4 * boxed, f"{backend}: the enlarged box selects {enlarged} pixels, before {boxed}"

            # its menu: only what is fully within, and the neighbour the box merely reaches into goes
            await page.mouse.click(*CENTRE, button="right")
            await page.get_by_role("menuitemradio", name="Fully within").click()
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(700)
            within = _count(await _pixels(page), _is_selected)
            assert within < 0.8 * enlarged, f"{backend}: fully within still selects {within} of {enlarged} pixels"
            assert within > 0.6 * one, f"{backend}: fully within lost the object the box holds ({within} pixels)"

            # without boxes and selection, a window dragged over the whole model selects all three objects
            await _command(page, "Remove selection boxes")
            await _command(page, "Clear selection")
            image = await _pixels(page)
            assert _box_edges(image) < 50, f"{backend}: a removed box is still drawn"
            assert _count(image, _is_selected) < 50, f"{backend}: the selection was not cleared"
            await page.keyboard.down("Shift")
            await page.mouse.move(305, 150)
            await page.mouse.down()
            await page.mouse.move(700, 500, steps=4)
            await page.mouse.move(1200, 790, steps=4)
            await page.mouse.up()
            await page.keyboard.up("Shift")
            await page.wait_for_timeout(800)
            windowed = _count(await _pixels(page), _is_selected)
            assert windowed > 2.2 * one, f"{backend}: the window selected {windowed} pixels, one object is {one}"
        finally:
            await browser.close()

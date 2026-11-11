"""What a model says beside its geometry (its ``ADA_EXT_data`` extension), on whichever viewport is on
screen: the glyphs of an analysis model's concepts, and the link between a design model and the
analysis model made from it."""

import asyncio
import json
import struct

import pytest
from PIL import Image
from playwright.async_api import Page, async_playwright

import ada
from ada.comms.wsock.client_async import WebSocketClientAsync

from .test_render_backends import (
    CENTRE,
    NATIVE,
    _click,
    _command,
    _count,
    _is_selected,
    _model,
    _model_on_screen,
    _open_viewer,
    _pixels,
    _until,
)

# The part of the page the model is drawn in, clear of the panels and the toolbars.
MODEL = (300, 120, 1000, 700)


def _with_extension(glb: bytes, **blocks) -> bytes:
    """`glb` with `blocks` put into its root ``ADA_EXT_data`` extension."""
    json_length = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20 : 20 + json_length])
    rest = glb[20 + json_length :]
    doc.setdefault("extensions", {}).setdefault("ADA_EXT_data", {}).update(blocks)
    used = doc.setdefault("extensionsUsed", [])
    if "ADA_EXT_data" not in used:
        used.append("ADA_EXT_data")
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    return (
        struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + len(rest))
        + struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + rest
    )


def _model_message(instance_id: int, name: str, glb: bytes) -> bytes:
    """A websocket message that adds a model, called `name`, to what is on screen."""
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

    file_object = FileObjectDC(name=name, file_type=FileTypeDC.GLB, purpose=FilePurposeDC.DESIGN, filedata=glb)
    return serialize_root_message(
        MessageDC(
            instance_id=instance_id,
            command_type=CommandTypeDC.UPDATE_SCENE,
            target_group=TargetTypeDC.WEB,
            target_id=1,
            scene=SceneDC(operation=SceneOperationsDC.ADD, current_file=file_object),
        )
    )


async def _send(ws_server, name: str, glb: bytes) -> None:
    async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as client:
        await client.websocket.send(_model_message(client.instance_id, name, glb))


def _glb(assembly: ada.Assembly, tmp_path, name: str) -> bytes:
    path = tmp_path / name
    assembly.to_gltf(path)
    return path.read_bytes()


def _is_mass(px) -> bool:  # a mass glyph's amber: no model, selection or toolbar colour
    return px[0] > 190 and 110 < px[1] < 200 and px[2] < 90


def _is_selected_concept(px) -> bool:  # the pale yellow a selected concept is drawn in
    return px[0] > 215 and px[1] > 200 and 70 < px[2] < 170


def _centre_of(image: Image.Image, predicate) -> tuple[int, int]:
    """The middle of the pixels of `image` that `predicate` takes."""
    pixels = image.load()
    points = [(x, y) for y in range(image.height) for x in range(image.width) if predicate(pixels[x, y])]
    assert points, "nothing of that colour is on screen"
    return sum(p[0] for p in points) // len(points), sum(p[1] for p in points) // len(points)


async def _in_model(page: Page, predicate) -> int:
    return _count((await _pixels(page)).crop(MODEL), predicate)


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_concept_glyphs_are_drawn_and_clicked_on_whichever_viewport_is_on_screen(ws_server, backend, tmp_path):
    """A model that carries analysis concepts shows them as glyphs over the model, a click on a glyph
    selects the concept in place of the model's objects, and a click on the model gives it back."""
    # A mass above the deck, clear of the box in the middle of the view.
    concepts = {"masses": [{"name": "pump", "position": [0.6, 1.0, 0.8], "mass": 1200.0}], "bcs": [], "scenarios": []}
    glb = _with_extension(_glb(_model(), tmp_path, "topside.glb"), design_objects=[{"name": "demo", "fem_concepts": concepts}])
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            # A concept model by its name: its concepts are switched on as it arrives.
            await _send(ws_server, "topside.xml", glb)
            await _until(page, lambda: _model_on_screen(page), "the model to be drawn")
            if backend == NATIVE:
                assert await page.evaluate("document.documentElement.dataset.nativeViewport") == "active"

            async def glyph_drawn():
                return await _in_model(page, _is_mass) > 40

            await _until(page, glyph_drawn, f"{backend}: the mass's glyph")
            assert await _in_model(page, _is_selected_concept) < 10

            at = _centre_of((await _pixels(page)).crop(MODEL), _is_mass)
            await _click(page, MODEL[0] + at[0], MODEL[1] + at[1])

            async def glyph_selected():
                return await _in_model(page, _is_selected_concept) > 40

            await _until(page, glyph_selected, f"{backend}: the clicked glyph to be drawn as selected")
            assert _count(await _pixels(page), _is_selected) < 500, f"{backend}: the click on the glyph selected the model"
            assert await page.get_by_text("pump").count() >= 1, f"{backend}: the selected mass is not described"

            # a click on the model: the object is selected, and the concept no longer is
            await _click(page, *CENTRE)
            assert _count(await _pixels(page), _is_selected) > 1500, f"{backend}: the click selected nothing"
            assert await _in_model(page, _is_selected_concept) < 10, f"{backend}: the concept stayed selected"
            assert await _in_model(page, _is_mass) > 40, f"{backend}: the glyph went with its selection"
        finally:
            await browser.close()


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_design_and_analysis_models_are_linked_on_whichever_viewport_is_on_screen(ws_server, backend, tmp_path):
    """Two models made from one design: an object of the design model names what it was meshed as,
    and the button beside it selects that in the other model."""
    design = _with_extension(
        _glb(_model(), tmp_path, "design.glb"),
        assembly_guid="one-design",
        design_objects=[{"name": "demo", "object_guids": {"box": "guid-of-box"}}],
    )
    annex = ada.Part("annex")
    annex.add_shape(ada.PrimBox("tower", (1.5, 2.5, 0.0), (2.5, 3.5, 2.0)))
    analysis = _with_extension(
        _glb(ada.Assembly("b") / annex, tmp_path, "analysis.glb"),
        assembly_guid="one-design",
        design_objects=[],
        simulation_objects=[{"name": "annex", "groups": [{"name": "lineage::box", "parent_object_guid": "guid-of-box", "members": ["tower"]}]}],
    )
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await _send(ws_server, "design.glb", design)
            await _until(page, lambda: _model_on_screen(page), "the design model to be drawn")
            await _command(page, "Selection tree")
            roots = page.locator("[role=treeitem][aria-level='1']")
            await _send(ws_server, "analysis.glb", analysis)

            async def two_roots():
                return await roots.count() == 2

            await _until(page, two_roots, f"{backend}: the second model's root in the Scene tree")

            # The analysis model's object, found on screen by selecting its model from the tree.
            await roots.nth(1).click()
            await page.wait_for_timeout(1000)
            at = _centre_of(await _pixels(page), _is_selected)
            await _command(page, "Clear selection")
            await _click(page, *at)
            assert _count(await _pixels(page), _is_selected) > 300, f"{backend}: the click selected nothing"
            # the panel's command is a switch: only when it is not already up
            if not await page.get_by_text("Selected Object Info").count():
                await _command(page, "Object info")
            properties = page.get_by_role("button", name="Properties")
            await _until(page, lambda: properties.count(), f"{backend}: the selected object's properties")
            if await properties.first.get_attribute("aria-expanded") != "true":
                await properties.first.click()

            tower_pixels = _count(await _pixels(page), _is_selected)

            # the analysis object names the design object it was made from
            back = page.get_by_role("button", name="Select in CAD")
            await _until(page, lambda: back.count(), f"{backend}: the link from the analysis object to the design")
            await back.first.click()

            # ... which is then the selection, and names what it was meshed as
            link = page.get_by_role("button", name="Select FEA elements")
            await _until(page, lambda: link.count(), f"{backend}: the link from the design object to its elements")
            box_pixels = _count(await _pixels(page), _is_selected)
            assert box_pixels > 300 and box_pixels != tower_pixels, (
                f"{backend}: the design object is not the one drawn selected ({box_pixels} pixels, {tower_pixels} before)"
            )
            await link.first.click()
            await _until(page, lambda: back.count(), f"{backend}: the analysis object to be the selection again")
            assert _count(await _pixels(page), _is_selected) == tower_pixels, f"{backend}: the analysis object is not drawn selected"
        finally:
            await browser.close()

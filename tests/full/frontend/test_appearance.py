"""The model looks the same whichever renderer draws it, and what a user changes about its look changes
on all of them: three.js (WebGL), the adacpp WebGPU renderer that mirrors the three.js scene, and the
three.js-free viewport.

What is compared is pixels of the page. Two renderers never give the same bytes, so nothing here asks
for that: a picture agrees with another when nearly all of its model pixels are within a few levels
(of 255) of the other's, and a number is right when it is within a few levels of what the lights give.

Tolerances, and where they come from:

* ``CAD_LEVELS``: a lit model surface on two renderers. The lights, the material and the colour
  space are the same; what is left is rounding. Measured: 0 to 1 level.
* ``RESULT_LEVELS``: one colour of a painted FEA result. three.js keeps vertex colours as floats, the
  adacpp renderer as 8 bits of linear light per channel, which moves the darkest channel values by up
  to 6.4 levels (``src/frontend/src/__tests__/utils/resultColourSpace.test.ts``) and the rest by less
  than one.
* ``RESULT_SPREAD``: a painted result on two renderers. A result is framed when it arrives, a few
  pixels differently from one load to the next, so the pictures are compared as colours, not as
  pixels: how far each channel's values, sorted, are from the other picture's on average. Measured:
  under 3 levels; a shaded result against an unshaded one is 15 and more.
* ``LEGEND_LEVELS``: how far a result's colours are from the nearest colour of its legend, on
  average. At a node the colour is the legend's; inside an element the renderers blend the nodes'
  colours in linear light while the legend's bar blends in sRGB, which on this coarse mesh is 4
  levels on average. A shaded result is 27 from it.
"""

import asyncio
import math

import numpy as np
import pytest
from PIL import Image
from playwright.async_api import Page, async_playwright

import ada

from .test_render_backends import (
    CENTRE,
    NATIVE,
    VIEWPORT,
    _click,
    _command,
    _count,
    _is_coloured,
    _is_model,
    _is_selected,
    _model,
    _model_on_screen,
    _open_viewer,
    _pixels,
    _steady,
    _until,
)

ALL = ["three", "adacpp", NATIVE]
CAD_LEVELS = 3
RESULT_LEVELS = 10
RESULT_SPREAD = 6
LEGEND_LEVELS = 9
# The part of the page the model is drawn in, clear of the toolbar, the panels and the gizmo.
VIEW = (380, 60, 1030, 760)
BACKGROUND_CLICK = (700, 100)

# A result is loaded without its element grid ("Skip element-edge wireframe" under Performance), so a
# surface is its result colour and nothing else: the grid's lines are drawn a pixel wide, and each
# renderer smooths such a line its own way.
NO_ELEMENT_GRID = "localStorage.setItem('ada-perf', JSON.stringify({state: {hideElementEdges: true}, version: 1}))"

# A value written into an input the way typing or dragging does: through the element's own setter,
# then the events a framework listens for.
SET_INPUT = """([selector, value]) => {
    const el = document.querySelector(selector);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
}"""


def _srgb(linear: float) -> float:
    """Linear light as an sRGB level, 0..255."""
    c = 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055
    return 255 * min(1.0, max(0.0, c))


def _lit(base: float, ndl: float, brightness: float = 1.0, metalness: float = 0.1, roughness: float = 0.7) -> float:
    """The sRGB level of a grey surface under the viewer's lights: an ambient light of irradiance pi / 2
    and a headlight of intensity 1.4, both times ``brightness``, on the material ada writes (three.js'
    MeshStandardMaterial: Lambert diffuse and a GGX highlight). ``ndl`` is the cosine between the
    face's normal and the direction to the camera."""
    ambient, headlight = brightness * math.pi / 2, brightness * 1.4
    diffuse = base * (1 - metalness) * (ambient + headlight * ndl) / math.pi
    a2 = roughness**4
    f0 = 0.04 * (1 - metalness) + base * metalness
    visibility = 0.5 / (2 * ndl * math.sqrt(a2 + (1 - a2) * ndl * ndl))
    distribution = a2 / (math.pi * (ndl * ndl * (a2 - 1) + 1) ** 2)
    return _srgb(diffuse + headlight * ndl * f0 * visibility * distribution)


def _is_red(px) -> bool:
    return px[0] > 150 and px[1] < 90 and px[2] < 90


def _patch(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    """The middle colour of the 5 x 5 pixels around a point: a line through it does not decide it."""
    return sorted(image.getpixel((x + i, y + j)) for i in range(-2, 3) for j in range(-2, 3))[12]


def _array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.crop(VIEW), dtype=np.int16)


def _agreement(a: Image.Image, b: Image.Image, mask) -> tuple[float, float, int]:
    """How alike two pictures of the same view are where ``mask`` says both show the model: the
    largest channel difference of each such pixel, as (its mean, its 99th percentile, the pixel count)."""
    x, y = _array(a), _array(b)
    both = mask(x) & mask(y)
    worst = np.abs(x - y).max(axis=2)[both]
    return float(worst.mean()), float(np.percentile(worst, 99)), int(both.sum())


def _spread(a: Image.Image, b: Image.Image, mask) -> float:
    """How alike the colours of two pictures are, wherever each is drawn: per channel, the mean
    distance between the two pictures' values taken in order of size; the largest of the three."""
    quantiles = np.linspace(0, 100, 501)
    x, y = _array(a), _array(b)
    x, y = x[mask(x)], y[mask(y)]
    assert len(x) > 20000 and len(y) > 20000, f"too little of the model is on screen ({len(x)}, {len(y)} pixels)"
    return max(
        float(np.abs(np.percentile(x[:, c], quantiles) - np.percentile(y[:, c], quantiles)).mean()) for c in range(3)
    )


def _assert_agree(a: Image.Image, b: Image.Image, mask, levels: int, what: str) -> None:
    mean, p99, count = _agreement(a, b, mask)
    assert count > 20000, f"{what}: only {count} pixels show the model on both renderers"
    assert p99 <= levels and mean <= levels / 3, (
        f"{what}: the two renderers differ by {mean:.2f} levels on average and {p99:.0f} at the 99th "
        f"percentile over {count} pixels (allowed: {levels})"
    )


def _grey_mask(x: np.ndarray) -> np.ndarray:  # a lit grey model surface, not its black edges
    return (x.min(axis=2) > 120) & ((x.max(axis=2) - x.min(axis=2)) < 12)


def _coloured_mask(x: np.ndarray) -> np.ndarray:  # a result colour: clearly not grey
    return (x.max(axis=2) - x.min(axis=2)) > 70


async def _show_cad(page: Page, ws_server) -> None:
    await asyncio.to_thread(lambda: _model().show(ws_port=ws_server.port, unique_viewer_id=1, liveness_timeout=10))
    await _until(page, lambda: _model_on_screen(page), "the model to be drawn")


async def _show_result(page: Page, ws_server, fem_files) -> None:
    result = ada.from_fem_res(fem_files / "cantilever/calculix/eigen_shell_cantilever_calculix.frd")
    await asyncio.to_thread(lambda: result.show(port=ws_server.port, unique_id=1, ping_timeout=10))

    async def coloured():
        return _count(await _pixels(page), _is_coloured) > 5000

    await _until(page, coloured, "the result field to be painted", seconds=40)


async def _appearance_options(page: Page) -> None:
    """Open Options at its Appearance section, the way the palette's "Appearance: …" commands do."""
    await _command(page, "Appearance: brightness")
    header = page.locator("button[aria-expanded]", has_text="Appearance").first
    await header.wait_for(timeout=10000)
    if await header.get_attribute("aria-expanded") == "false":
        await header.click()
    await page.locator("[data-option=brightness]").wait_for(timeout=10000)


async def _set_option(page: Page, option: str, value: str) -> None:
    await page.evaluate(SET_INPUT, [f"[data-option={option}]", value])


async def _set_colour(page: Page, colour: str, expect_scope: str) -> None:
    """The "Set colour of selection…" command: its dialog says what it applies to, takes a colour, applies."""
    await _command(page, "Set colour of selection")
    dialog = page.get_by_test_id("object-colour-dialog")
    await dialog.wait_for(timeout=10000)
    scope = await page.get_by_test_id("object-colour-scope").inner_text()
    assert expect_scope in scope, f"the dialog says {scope!r}, expected it to name {expect_scope!r}"
    await page.evaluate(SET_INPUT, ["[data-testid=object-colour-input]", colour])
    await dialog.get_by_role("button", name="Apply").click()


@pytest.mark.parametrize("backend", ["adacpp", NATIVE])
async def test_a_cad_model_looks_the_same_as_on_three(ws_server, backend):
    """Default settings, the same model, the same views: the lit surfaces are the same colours, and
    they are the colours the lights give."""
    async with async_playwright() as playwright:
        pictures = {}
        for which in ("three", backend):
            browser, page, _ = await _open_viewer(playwright, ws_server, which)
            try:
                await _show_cad(page, ws_server)
                pictures[which, "iso"] = await _steady(page, VIEW)
                await _command(page, "Front view")
                pictures[which, "front"] = await _steady(page, VIEW)
            finally:
                await browser.close()
        for view in ("iso", "front"):
            _assert_agree(
                pictures["three", view], pictures[backend, view], _grey_mask, CAD_LEVELS, f"{backend}, {view}"
            )
        # From the front the box's face looks at the camera, and the deck is seen at a grazing angle.
        for which in ("three", backend):
            face = _patch(pictures[which, "front"], *CENTRE)
            assert abs(face[0] - _lit(0.8, 1.0)) <= CAD_LEVELS, f"{which}: the face towards the camera is {face}"
            iso = _patch(pictures[which, "iso"], CENTRE[0] + 60, CENTRE[1] + 30)
            assert abs(iso[0] - _lit(0.8, 1 / math.sqrt(3))) <= CAD_LEVELS, f"{which}: a face seen at an angle is {iso}"


@pytest.mark.parametrize("backend", ["adacpp", NATIVE])
async def test_an_fea_result_is_the_same_colours_as_on_three_and_they_are_the_legends(ws_server, backend, fem_files):
    """A result colour means the same value on every renderer: the painted surface is the same
    colours as on three.js, and each of them is a colour of the legend beside it. Shading the result
    (an appearance setting) darkens it the same way on both."""
    async with async_playwright() as playwright:
        flat, shaded = {}, {}
        for which in ("three", backend):
            browser, page, _ = await _open_viewer(playwright, ws_server, which, init_script=NO_ELEMENT_GRID)
            try:
                await _show_result(page, ws_server, fem_files)
                flat[which] = await _steady(page, VIEW)
                await _command(page, "Appearance: shade FEA results")
                shaded[which] = await _steady(page, VIEW)
            finally:
                await browser.close()

        def same_colours(a, b, what: str) -> None:
            spread = _spread(a, b, _coloured_mask)
            assert spread <= RESULT_SPREAD, f"{backend}, {what}: its colours are {spread:.1f} levels from three.js'"

        same_colours(flat["three"], flat[backend], "result")
        for which in ("three", backend):
            # The legend's bar is the colour map as CSS draws it: every colour a value can have.
            image = flat[which]
            bar = np.asarray(image.crop((1047, 402, 1063, 650)), dtype=np.int16)
            legend = np.unique(bar.reshape(-1, 3), axis=0)
            assert len(legend) > 100, f"{which}: the legend's colour bar was not found ({len(legend)} colours)"
            surface = _array(image)
            model, pixels = np.unique(surface[_coloured_mask(surface)].reshape(-1, 3), axis=0, return_counts=True)
            nearest = np.abs(model[:, None, :] - legend[None, :, :]).max(axis=2).min(axis=1)
            mean = float(np.average(nearest, weights=pixels))
            assert mean <= LEGEND_LEVELS, f"{which}: the result's colours are {mean:.1f} levels from the legend's"
            # The largest value is on the model, in exactly the colour at the top of the bar.
            top = np.abs(model - bar[0, bar.shape[1] // 2]).max(axis=1)
            assert pixels[top <= RESULT_LEVELS].sum() > 50, (
                f"{which}: the legend's top colour {tuple(bar[0, bar.shape[1] // 2])} is not on the model "
                f"(nearest: {int(top.min())} levels off)"
            )

        # Shaded, the surface is darker than its legend colour, by the same amount on both.
        same_colours(shaded["three"], shaded[backend], "shaded result")
        for which in ("three", backend):
            dimmed = _spread(flat[which], shaded[which], _coloured_mask)
            assert (
                dimmed > 2 * RESULT_SPREAD
            ), f"{which}: shading the result changed its colours by {dimmed:.1f} levels only"


@pytest.mark.parametrize("backend", ALL)
async def test_brightness_the_default_colour_and_the_background_change_at_once(ws_server, backend):
    """The Options panel's Appearance section, on a model that is on screen: each change is drawn at
    once, to the same numbers on every renderer, and Reset gives the defaults back."""
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await _show_cad(page, ws_server)
            await _command(page, "Front view")
            face = (CENTRE[0] + 40, CENTRE[1])
            corner = (VIEWPORT["width"] - 300, 120)

            async def face_is(level: float, what: str):
                async def there():
                    return abs(_patch(await _pixels(page), *face)[0] - level) <= CAD_LEVELS

                await _until(page, there, f"{backend}: the face to be {level:.0f} ({what})")

            await face_is(_lit(0.8, 1.0), "the default look")
            await _appearance_options(page)

            await _set_option(page, "brightness", "0.5")
            await face_is(_lit(0.8, 1.0, brightness=0.5), "half the light")
            await _set_option(page, "brightness", "1.5")
            await face_is(_lit(0.8, 1.0, brightness=1.5), "half as much light again")

            # All of the light ambient: no headlight, so no highlight and every face the same.
            await _set_option(page, "brightness", "1")
            await _set_option(page, "light-balance", "1")
            await face_is(_srgb(0.8 * 0.9 * (math.pi / 2 + 1.4) / math.pi), "ambient light only")

            await page.locator("[data-option=appearance-reset]").click()
            await face_is(_lit(0.8, 1.0), "reset")

            # The model has no colour of its own: it takes the default colour.
            await _set_option(page, "default-colour", "#ff0000")

            async def red():
                return _is_red(_patch(await _pixels(page), *face))

            await _until(page, red, f"{backend}: the model to take the default colour")

            await _set_option(page, "background-colour", "#204060")

            async def blue_background():
                px = _patch(await _pixels(page), *corner)
                return max(abs(px[0] - 0x20), abs(px[1] - 0x40), abs(px[2] - 0x60)) <= CAD_LEVELS

            await _until(page, blue_background, f"{backend}: the background to change")

            await page.locator("[data-option=appearance-reset]").click()
            await face_is(_lit(0.8, 1.0), "reset after the colours")
            assert max(_patch(await _pixels(page), *corner)) < 80, f"{backend}: the background did not go back"
        finally:
            await browser.close()


@pytest.mark.parametrize("backend", ALL)
async def test_recolour_the_selection_everything_reset_undo_and_redo(ws_server, backend):
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await _show_cad(page, ws_server)
            grey = _count(await _steady(page, VIEW), _is_model)

            async def reds(check, what: str) -> int:
                async def ok():
                    # The model's part of the page: the gizmo in the corner has a red axis of its own.
                    n = _count((await _pixels(page)).crop(VIEW), _is_red)
                    return (n,) if check(n) else None

                return (await _until(page, ok, f"{backend}: {what}"))[0]

            # the selection: the box in the middle of the view
            await _click(page, *CENTRE)
            assert _count(await _pixels(page), _is_selected) > 3000, f"{backend}: the click selected nothing"
            await _set_colour(page, "#ff0000", "selected object")
            await page.wait_for_timeout(500)
            # the highlight is still what a selected object shows, recoloured or not
            assert _count(await _pixels(page), _is_selected) > 3000, f"{backend}: the recoloured box lost its highlight"
            await _click(page, *BACKGROUND_CLICK)
            box = await reds(lambda n: n > 3000, "the box to be red once it is no longer selected")
            still_grey = _count(await _pixels(page), _is_model)
            assert still_grey > 0.4 * grey, f"{backend}: the deck lost its colour too ({still_grey} of {grey})"

            # hiding and showing keep the colour
            await _click(page, *CENTRE)
            await _command(page, "Hide selected")
            await reds(lambda n: n < 50, "the hidden box to be gone")
            await _command(page, "Show all")
            await reds(lambda n: abs(n - box) < 0.1 * box, "the box to come back red")

            # so does a section plane
            await _command(page, "Section plane X")
            await reds(lambda n: 0.1 * box < n < 0.9 * box, "the plane to cut the red box")
            await _command(page, "Remove section planes")
            await reds(lambda n: abs(n - box) < 0.1 * box, "the whole box again")

            # nothing selected: every object
            await _set_colour(page, "#ff0000", "every object")
            everything = await reds(lambda n: n > 2 * box, "the whole model to be red")
            assert _count(await _pixels(page), _is_model) < 0.05 * grey, f"{backend}: part of the model stayed grey"

            await _command(page, "Reset colours")
            await reds(lambda n: n < 50, "the colours to be reset")
            assert (
                _count(await _pixels(page), _is_model) > 0.9 * grey
            ), f"{backend}: the model did not get its colour back"

            # undo and redo: one step per action
            await page.keyboard.press("Control+z")
            await reds(lambda n: abs(n - everything) < 0.1 * everything, "undo to bring every colour back")
            await page.keyboard.press("Control+z")
            await reds(lambda n: abs(n - box) < 0.1 * box, "a second undo to leave the box red only")
            await page.keyboard.press("Control+y")
            await reds(lambda n: abs(n - everything) < 0.1 * everything, "redo to colour everything again")
        finally:
            await browser.close()


@pytest.mark.parametrize("backend", ["three", NATIVE])
async def test_the_elements_of_a_result_are_not_recoloured_and_the_dialog_says_so(ws_server, backend, fem_files):
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend)
        try:
            await _show_result(page, ws_server, fem_files)
            before = _count(await _steady(page, VIEW), _is_coloured)
            await _set_colour(page, "#ff00ff", "every object")
            note = page.get_by_test_id("object-colour-note")
            await note.wait_for(timeout=10000)
            assert "FEA result" in await note.inner_text()
            # The dialog is still open over the page: closed, the result is as it was.
            await page.get_by_test_id("object-colour-dialog").get_by_role("button", name="Cancel").click()
            after = _count(await _steady(page, VIEW), _is_coloured)
            assert abs(after - before) < 0.02 * before, f"{backend}: the result's colours changed ({before} -> {after})"
        finally:
            await browser.close()

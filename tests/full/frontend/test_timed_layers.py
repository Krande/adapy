"""A recorded run replayed in the viewer: a model whose parts move over time and layers of data that
change with it (an image on the ground, a line, a set of points), all on one playback clock, on
whichever viewport is on screen. Also the orbit pivot a double-click sets, the Layers panel, and a
Draco-compressed model.

What is asserted is what a user sees: pixels of the page and the transport's own controls.
"""

import base64
import json
import struct

import numpy as np
import pytest
from playwright.async_api import Page, async_playwright

from .test_render_backends import (
    NATIVE,
    VIEWPORT,
    _assert_drawing_with,
    _click,
    _command,
    _open_viewer,
    _pixels,
    _until,
)

# Asks the viewer, before it starts, for what a plugin draws and plays with (plugins/index.ts).
SCRIPTED_SCENE = "window.__adaScriptedScene = true;"
SCENE = "window.__adaScriptedScene"
# How long the test model's animation, and the layers' track, are: seconds.
DURATION = 2.0
# The model's second, shorter animation: seconds.
RISE = 1.0


# The three ways a picture is made: three.js, the adacpp WebGPU renderer mirroring the three.js scene,
# and the three.js-free viewport.
WAYS = ["three", "adacpp", NATIVE]


def _box(lo, hi) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A box as 24 vertices (four a face, so each face has its own normal), their normals, 36 indices."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = [
        ((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
        ((-1, 0, 0), [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)]),
        ((0, 1, 0), [(x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)]),
        ((0, -1, 0), [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
        ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
        ((0, 0, -1), [(x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0)]),
    ]
    positions = np.array([p for _, corners in faces for p in corners], dtype=np.float32)
    normals = np.array([n for n, _ in faces for _ in range(4)], dtype=np.float32)
    indices = np.array([[4 * f + k for k in (0, 1, 2, 0, 2, 3)] for f in range(6)], dtype=np.uint32).ravel()
    return positions, normals, indices


def _animated_glb() -> bytes:
    """A grey plate with a green block and a small orange one on it, and two animations: the green
    block's parent node slides along x over `DURATION` seconds, the orange one's rises over `RISE`
    seconds. Z is up, as in the viewer's world."""
    chunks: list[bytes] = []
    views: list[dict] = []
    accessors: list[dict] = []

    def accessor(array: np.ndarray, kind: str, component: int, bounds: bool = False) -> int:
        data = array.tobytes()
        offset = sum(len(c) for c in chunks)
        chunks.append(data + b"\0" * (-len(data) % 4))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
        entry = {"bufferView": len(views) - 1, "componentType": component, "count": len(array), "type": kind}
        if bounds:
            entry["min"] = array.min(axis=0).tolist() if array.ndim > 1 else [float(array.min())]
            entry["max"] = array.max(axis=0).tolist() if array.ndim > 1 else [float(array.max())]
        accessors.append(entry)
        return len(accessors) - 1

    def mesh(lo, hi, material: int) -> dict:
        positions, normals, indices = _box(lo, hi)
        attributes = {"POSITION": accessor(positions, "VEC3", 5126, True), "NORMAL": accessor(normals, "VEC3", 5126)}
        return {
            "primitives": [
                {"attributes": attributes, "indices": accessor(indices, "SCALAR", 5125), "material": material}
            ]
        }

    meshes = [
        mesh((0, 0, -0.1), (4, 2, 0), 0),
        mesh((-0.3, -0.3, 0), (0.3, 0.3, 0.6), 1),
        mesh((-0.15, -0.15, 0), (0.15, 0.15, 0.3), 2),
    ]
    times = accessor(np.array([0.0, DURATION], dtype=np.float32), "SCALAR", 5126, True)
    places = accessor(np.array([[0.5, 1.0, 0.0], [3.5, 1.0, 0.0]], dtype=np.float32), "VEC3", 5126)
    rise_times = accessor(np.array([0.0, RISE], dtype=np.float32), "SCALAR", 5126, True)
    heights = accessor(np.array([[3.6, 0.3, 0.0], [3.6, 0.3, 1.0]], dtype=np.float32), "VEC3", 5126)
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0, 1, 3]}],
        "nodes": [
            {"name": "plate", "mesh": 0},
            {"name": "carriage", "translation": [0.5, 1.0, 0.0], "children": [2]},
            {"name": "block", "mesh": 1},
            {"name": "lift", "translation": [3.6, 0.3, 0.0], "children": [4]},
            {"name": "flag", "mesh": 2},
        ],
        "meshes": meshes,
        "materials": [
            {"name": "grey", "pbrMetallicRoughness": {"baseColorFactor": [0.6, 0.6, 0.6, 1], "metallicFactor": 0}},
            {"name": "green", "pbrMetallicRoughness": {"baseColorFactor": [0.0, 0.6, 0.0, 1], "metallicFactor": 0}},
            {"name": "orange", "pbrMetallicRoughness": {"baseColorFactor": [1.0, 0.15, 0.0, 1], "metallicFactor": 0}},
        ],
        "animations": [
            {
                "name": "slide",
                "samplers": [{"input": times, "output": places, "interpolation": "LINEAR"}],
                "channels": [{"sampler": 0, "target": {"node": 1, "path": "translation"}}],
            },
            {
                "name": "rise",
                "samplers": [{"input": rise_times, "output": heights, "interpolation": "LINEAR"}],
                "channels": [{"sampler": 0, "target": {"node": 3, "path": "translation"}}],
            },
        ],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": sum(len(c) for c in chunks)}],
    }
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    binary = b"".join(chunks)
    length = 12 + 8 + len(text) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, length)
        + struct.pack("<I4s", len(text), b"JSON")
        + text
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


# A unit cube as one Draco-compressed primitive (KHR_draco_mesh_compression, required): 8 vertices,
# 12 triangles, positions only. Encoded once with the encoder three.js ships.
DRACO_CUBE_GLB = (
    "Z2xURgIAAADAAgAAVAIAAEpTT057ImFzc2V0Ijp7InZlcnNpb24iOiIyLjAifSwic2NlbmUiOjAsInNjZW5lcyI6W3sibm9kZXMi"
    "OlswXX1dLCJub2RlcyI6W3sibmFtZSI6ImN1YmUiLCJtZXNoIjowfV0sIm1lc2hlcyI6W3sicHJpbWl0aXZlcyI6W3siYXR0cmli"
    "dXRlcyI6eyJQT1NJVElPTiI6MH0sImluZGljZXMiOjEsIm1vZGUiOjQsImV4dGVuc2lvbnMiOnsiS0hSX2RyYWNvX21lc2hfY29t"
    "cHJlc3Npb24iOnsiYnVmZmVyVmlldyI6MCwiYXR0cmlidXRlcyI6eyJQT1NJVElPTiI6MH19fX1dfV0sImFjY2Vzc29ycyI6W3si"
    "Y29tcG9uZW50VHlwZSI6NTEyNiwiY291bnQiOjgsInR5cGUiOiJWRUMzIiwibWluIjpbMCwwLDBdLCJtYXgiOlsxLDEsMV19LHsi"
    "Y29tcG9uZW50VHlwZSI6NTEyNSwiY291bnQiOjM2LCJ0eXBlIjoiU0NBTEFSIn1dLCJidWZmZXJWaWV3cyI6W3siYnVmZmVyIjow"
    "LCJieXRlT2Zmc2V0IjowLCJieXRlTGVuZ3RoIjo3OX1dLCJidWZmZXJzIjpbeyJieXRlTGVuZ3RoIjo4MH1dLCJleHRlbnNpb25z"
    "VXNlZCI6WyJLSFJfZHJhY29fbWVzaF9jb21wcmVzc2lvbiJdLCJleHRlbnNpb25zUmVxdWlyZWQiOlsiS0hSX2RyYWNvX21lc2hf"
    "Y29tcHJlc3Npb24iXX0gIFAAAABCSU4ARFJBQ08CAgEBAAAACAwACwAAA2+rFAEBEAH/AAABAAkDAAACAQEBAAMDATABEAMAmJYU"
    "GAAEAAAAAP8/AAAAAAAAAAAAAAAAAAAAAIA/DgA="
)


async def _seen(page: Page) -> dict:
    """How many pixels of each of the test's colours are on screen, and where the middle of the block is."""
    rgb = np.asarray(await _pixels(page)).astype(np.int32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    green = (g > 90) & (r < 0.6 * g) & (b < 0.6 * g)
    ys, xs = np.nonzero(green)
    orange = (r > 140) & (g > 0.2 * r) & (g < 0.7 * r) & (b < 0.25 * r)
    oys, oxs = np.nonzero(orange)
    red = (r > 200) & (g < 60) & (b < 60)
    rys, rxs = np.nonzero(red)
    magenta = (r > 200) & (g < 60) & (b > 200)
    mys, mxs = np.nonzero(magenta)
    return {
        "green": int(green.sum()),
        "block": (float(xs.mean()), float(ys.mean())) if len(xs) else None,
        "magenta": int(magenta.sum()),
        "cyan": int(((r < 90) & (g > 180) & (b > 180)).sum()),
        "yellow": int(((r > 180) & (g > 180) & (b < 90)).sum()),
        "flag": (float(oxs.mean()), float(oys.mean())) if len(oxs) > 100 else None,
        "image": (float(mxs.mean()), float(mys.mean())) if len(mxs) > 100 else None,
        "marker": (float(rxs.mean()), float(rys.mean())) if len(rxs) > 20 else None,
        "block_box": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None,
    }


async def _load_model(page: Page, glb: bytes, name: str) -> None:
    await page.evaluate(
        f"""async (b64) => {{
            const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
            await {SCENE}.loadModel("t", {{bytes, sourceName: {json.dumps(name)}}});
        }}""",
        base64.b64encode(glb).decode(),
    )


async def _block_on_screen(page: Page) -> dict | None:
    seen = await _seen(page)
    return seen if seen["green"] > 1500 else None


async def _playback(page: Page) -> dict:
    return await page.evaluate(f"({{...{SCENE}.playback.state()}})")


async def _seek(page: Page, seconds: float) -> dict:
    await page.evaluate(f"{SCENE}.playback.seek({seconds})")
    await page.wait_for_timeout(700)
    return await _seen(page)


def _distance(a, b) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


async def _orbit(page: Page, dx: int) -> None:
    """Drag over the background, which orbits the camera about its pivot."""
    x, y = 200, VIEWPORT["height"] - 120
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + dx, y, steps=10)
    await page.mouse.up()
    # A drag may glide on after it is released.
    await page.wait_for_timeout(1500)


@pytest.mark.parametrize("backend", WAYS)
async def test_a_models_node_animation_plays_and_scrubs_on_the_playback_clock(ws_server, backend):
    """A GLB whose node moves over time is a track of the playback clock as soon as it is loaded: the
    transport appears, scrubbing shows the moment, and playing moves the part."""
    async with async_playwright() as playwright:
        browser, page, state = await _open_viewer(playwright, ws_server, backend, SCRIPTED_SCENE)
        try:
            transport = page.locator("[data-playback-transport]")
            assert await transport.count() == 0, "a transport with nothing to play"
            await _load_model(page, _animated_glb(), "sliding.glb")
            start = await _until(page, lambda: _block_on_screen(page), "the model to be drawn")
            await _assert_drawing_with(page, backend, state)

            # the model's animation is the clock's one track
            await _until(page, transport.count, "the transport to appear")
            clock = await _playback(page)
            assert clock["tracks"] == 1 and abs(clock["duration"] - DURATION) < 1e-6, clock
            assert not clock["playing"]

            # scrub: the block is where the animation has it at that moment
            end = await _seek(page, DURATION)
            travelled = _distance(start["block"], end["block"])
            assert travelled > 150, f"the block moved {travelled:.0f} px between the ends of the animation"
            half = await _seek(page, DURATION / 2)
            assert 0.3 * travelled < _distance(start["block"], half["block"]) < 0.7 * travelled
            back = await _seek(page, 0)
            assert _distance(start["block"], back["block"]) < 5

            # the file's second animation plays on the same clock, and holds its last moment past its end
            assert start["flag"] and end["flag"], f"{backend}: the orange block is not drawn"
            risen = _distance(start["flag"], end["flag"])
            assert risen > 40, f"{backend}: the second animation's block moved {risen:.0f} px"
            late = await _seek(page, (RISE + DURATION) / 2)
            assert _distance(late["flag"], end["flag"]) < 5, f"{backend}: past its end the second animation moved on"
            early = await _seek(page, RISE / 2)
            assert 0.25 * risen < _distance(start["flag"], early["flag"]) < 0.75 * risen
            await _seek(page, 0)

            # a part that has moved is picked, selected and hidden where it is now
            await _seek(page, DURATION)
            await _click(page, *end["block"])
            picked = np.asarray(await _pixels(page)).astype(np.int32)
            selected = (picked[..., 2] > 200) & (picked[..., 0] < 45) & (picked[..., 1] < 45)
            assert (
                int(selected.sum()) > 1500
            ), f"{backend}: a click on the moved block selected {int(selected.sum())} pixels"
            ys, xs = np.nonzero(selected)
            assert (
                _distance((xs.mean(), ys.mean()), end["block"]) < 40
            ), f"{backend}: the selection is not where the block is"
            await _command(page, "Hide selected")
            # (the orientation gizmo's own green stays)
            assert (await _seen(page))["green"] < 0.1 * end["green"], f"{backend}: the hidden block is still drawn"
            await _command(page, "Show all")
            assert _distance((await _seen(page))["block"], end["block"]) < 5
            await _seek(page, 0)

            # the transport's own scrub bar does the same
            await transport.get_by_label("Time").fill(f"{DURATION:g}")
            await page.wait_for_timeout(700)
            assert _distance((await _seen(page))["block"], end["block"]) < 5

            # play: from the end it starts over, the block moves, and it stops at the end
            await transport.get_by_label("Speed").select_option("0.5")
            await transport.get_by_role("button", name="Play").click()
            await page.wait_for_timeout(1500)
            clock = await _playback(page)
            assert clock["playing"] and 0.2 < clock["time"] < DURATION - 0.2, clock
            moving = (await _seen(page))["block"]
            assert _distance(moving, start["block"]) > 20 and _distance(moving, end["block"]) > 20
            await transport.get_by_role("button", name="Pause").click()
            paused = await _playback(page)
            assert not paused["playing"]
            await page.wait_for_timeout(500)
            assert (await _playback(page))["time"] == paused["time"], "the clock moved while paused"

            await page.evaluate(f"{SCENE}.playback.setSpeed(4); {SCENE}.playback.play()")

            async def ended():
                clock = await _playback(page)
                return not clock["playing"] and clock["time"] == clock["duration"]

            await _until(page, ended, "the clock to stop at the end")
            await page.wait_for_timeout(300)
            assert _distance((await _seen(page))["block"], end["block"]) < 5
        finally:
            await browser.close()


# What a plugin does to replay a run: three layers, and one track that shows each of them at a time.
# The image shows more columns as time passes, the line gets longer, the points get more numerous.
ADD_LAYERS = f"""() => {{
    const S = {SCENE};
    const W = 8, H = 4;
    const pixels = new Uint8Array(W * H * 4);
    const line = new Float32Array(3 * 32);
    const points = new Float32Array(3 * 64);
    const run = {{}};
    run.map = S.addImage("t", {{
        origin: [0, 0, 0.02], u: [4, 0, 0], v: [0, 2, 0], width: W, height: H, pixels, nearest: true,
        layer: {{id: "map", label: "Map", group: "Run"}},
    }});
    run.path = S.addLineLayer("t", {{color: "#00ffff", widthPx: 6, throughModel: true, layer: {{id: "path", label: "Path", group: "Run"}}}});
    run.scan = S.addPointLayer("t", {{color: "#ffff00", sizePx: 14, throughModel: true, layer: {{id: "scan", label: "Scan", group: "Run"}}}});
    run.shown = 0;
    run.track = S.playback.addTrack({{id: "layers", label: "Layers", duration: {DURATION}, show: (t) => {{
        run.shown++;
        const columns = 1 + Math.round(t / {DURATION} * (W - 1));
        for (let j = 0; j < H; j++) for (let i = 0; i < W; i++) {{
            const p = (j * W + i) * 4;
            pixels[p] = pixels[p + 2] = 255;
            pixels[p + 1] = 0;
            pixels[p + 3] = i < columns ? 255 : 0;
        }}
        run.map.setPixels(pixels);
        const vertices = 2 + Math.round(t * 12);
        for (let k = 0; k < vertices; k++) line.set([0.1 + k * 0.14, 0.3, 0.3 + 0.05 * (k % 2)], k * 3);
        run.path.setPolyline(line, vertices);
        const count = 2 + Math.round(t * 20);
        for (let k = 0; k < count; k++) points.set([0.1 + k * 0.09, 1.7, 0.2], k * 3);
        run.scan.setPoints(points, count);
    }}}});
    window.__run = run;
}}"""


@pytest.mark.parametrize("backend", WAYS)
async def test_layers_change_over_time_on_whichever_viewport_is_on_screen(ws_server, backend):
    """An image on the ground, a line and points, replaced by a track of the playback clock as its time
    changes; the Layers panel switches them; a double-click sets what the camera orbits about; and all
    of it goes when the plugin's overlays are released."""
    async with async_playwright() as playwright:
        browser, page, state = await _open_viewer(playwright, ws_server, backend, SCRIPTED_SCENE)
        try:
            await _load_model(page, _animated_glb(), "sliding.glb")
            await _until(page, lambda: _block_on_screen(page), "the model to be drawn")
            await _assert_drawing_with(page, backend, state)
            await page.wait_for_timeout(500)

            # ---- coordinates: a layer is given in the model's own coordinates, and lands on the model
            # wherever the viewport put it (the three.js viewport recentred the model when it loaded it).
            # The middle of the block's top face, in the file's coordinates, at the start of the animation.
            await page.evaluate(
                f"""window.__marker = {SCENE}.addPointLayer("t", {{
                    positions: [0.5, 1.0, 0.6], color: "#ff0000", sizePx: 16, throughModel: true}})"""
            )
            await page.wait_for_timeout(600)
            seen = await _seen(page)
            assert seen["marker"], f"{backend}: the marker is not drawn"
            x0, y0, x1, y1 = seen["block_box"]
            assert (
                x0 < seen["marker"][0] < x1 and y0 - 8 < seen["marker"][1] < y1
            ), f"{backend}: a point at the block's top lands at {seen['marker']}, the block is at {seen['block_box']}"
            await page.evaluate("window.__marker.remove()")
            await page.wait_for_timeout(400)
            assert (await _seen(page))["marker"] is None

            # ---- an image of a size a grid of cells has is drawn, and replacing its pixels is cheap
            timings = await page.evaluate(
                f"""() => {{
                    const out = {{}};
                    for (const side of [384, 512]) {{
                        const pixels = new Uint8Array(side * side * 4).fill(255);
                        const t0 = performance.now();
                        const image = {SCENE}.addImage("probe", {{
                            origin: [0, 0, 0.02], u: [4, 0, 0], v: [0, 2, 0], width: side, height: side, pixels, nearest: true}});
                        const t1 = performance.now();
                        for (let i = 0; i < pixels.length; i += 4) pixels[i + 1] = 0;
                        const t2 = performance.now();
                        for (let k = 0; k < 5; k++) image.setPixels(pixels);
                        out[side] = {{add: t1 - t0, set: (performance.now() - t2) / 5, unavailable: image.unavailable()}};
                        if (side === 384) window.__big = image; else image.remove();
                    }}
                    return out;
                }}"""
            )
            print(f"{backend}: image timings (ms) {timings}")
            assert timings["384"]["unavailable"] is None and timings["512"]["unavailable"] is None, timings
            await page.wait_for_timeout(800)
            assert (await _seen(page))["magenta"] > 20000, f"{backend}: a 384 x 384 image is not drawn"
            await page.evaluate(f'{SCENE}.remove("probe")')
            await page.wait_for_timeout(500)
            assert (await _seen(page))["magenta"] == 0
            assert await page.evaluate(f"{SCENE}.imageUnavailable(4096, 4096)") is None

            # ---- layers: nothing of them before they are added, and no button for the panel
            seen = await _seen(page)
            assert seen["magenta"] == seen["cyan"] == seen["yellow"] == 0, seen
            layers_button = page.get_by_role("button", name="Toggle layers panel")
            assert await layers_button.count() == 0, f"{backend}: a Layers button with no layer registered"
            assert await page.evaluate(f"{SCENE}.imageUnavailable(8, 4)") is None
            await page.evaluate(ADD_LAYERS)
            clock = await _playback(page)
            # The model's own animation is the other track.
            assert clock["tracks"] == 2, clock
            assert abs(clock["duration"] - DURATION) < 1e-6
            await _until(page, page.locator("[data-playback-transport]").count, "the transport to appear")

            # ---- scrub: each layer shows the moment the clock is at
            first = await _seek(page, 0)
            assert first["magenta"] > 300 and first["cyan"] > 30 and first["yellow"] > 30, f"{backend}: {first}"
            last = await _seek(page, DURATION)
            assert last["magenta"] > 3 * first["magenta"], f"{backend}: the image did not change: {first} -> {last}"
            assert last["cyan"] > 3 * first["cyan"], f"{backend}: the line did not change: {first} -> {last}"
            assert last["yellow"] > 3 * first["yellow"], f"{backend}: the points did not change: {first} -> {last}"
            middle = await _seek(page, DURATION / 2)
            for colour in ("magenta", "cyan", "yellow"):
                assert first[colour] < middle[colour] < last[colour], f"{backend}: {colour} {first} {middle} {last}"
            # The model's moving part and the layers show the same moment.
            assert _distance(first["block"], last["block"]) > 80, f"{backend}: the block did not move"
            # The image's first column lies at its origin, where the block starts: not at the far end.
            assert _distance(first["image"], first["block"]) < 0.5 * _distance(
                first["image"], last["block"]
            ), f"{backend}: the image's first column is not at its origin: {first}, block ends at {last['block']}"
            # back to fewer: what was drawn beyond the new count is gone
            again = await _seek(page, 0)
            for colour in ("magenta", "cyan", "yellow"):
                assert (
                    abs(again[colour] - first[colour]) <= 0.1 * first[colour] + 20
                ), f"{backend}: {colour} {again} {first}"

            # ---- play: the track is shown once per frame while it plays, and not at all afterwards
            shown = await page.evaluate("window.__run.shown")
            await page.evaluate(f"{SCENE}.playback.setSpeed(2); {SCENE}.playback.play()")

            async def ended():
                clock = await _playback(page)
                return not clock["playing"] and clock["time"] == clock["duration"]

            await _until(page, ended, "the clock to stop at the end")
            played = await page.evaluate("window.__run.shown") - shown
            assert played >= 5, f"{backend}: the track was shown {played} times while it played"
            await page.wait_for_timeout(600)
            resting = await page.evaluate("window.__run.shown")
            await page.wait_for_timeout(600)
            assert await page.evaluate("window.__run.shown") == resting, f"{backend}: the track is shown while paused"
            ended_at = await _seen(page)
            for colour in ("magenta", "cyan", "yellow"):
                assert abs(ended_at[colour] - last[colour]) <= 0.1 * last[colour] + 20, f"{backend}: {colour}"

            # ---- a look per handle: hidden and shown without making the layer again
            await page.evaluate("window.__run.path.setLook({visible: false})")
            await page.wait_for_timeout(500)
            assert (await _seen(page))["cyan"] == 0, f"{backend}: a hidden line is still drawn"
            await page.evaluate("window.__run.path.setLook({visible: true})")
            await page.wait_for_timeout(500)
            assert (await _seen(page))["cyan"] > 0.8 * last["cyan"]

            # ---- the Layers panel: a row per layer, a switch each, remembered in the browser
            await layers_button.click()
            panel = page.locator("[data-layers-panel]")
            await _until(page, panel.count, "the Layers panel")
            assert await panel.locator("[data-layer-id]").count() == 3
            await panel.get_by_role("switch", name="Show Map").uncheck()
            await page.wait_for_timeout(500)
            off = await _seen(page)
            assert off["magenta"] == 0, f"{backend}: the image is drawn although its layer is switched off"
            assert off["yellow"] > 0.8 * last["yellow"], f"{backend}: switching one layer off took another"
            kept = await page.evaluate("JSON.parse(localStorage.getItem('ada-layer-look'))")
            assert kept["t:map"]["visible"] is False, kept
            await panel.get_by_role("switch", name="Show Map").check()
            await panel.get_by_label("Opacity of Map").fill("0.5")
            await page.wait_for_timeout(500)
            faded = await _seen(page)
            assert (
                faded["magenta"] < 0.1 * last["magenta"] + 20
            ), f"{backend}: a half-opaque image is still pure magenta"
            assert (await page.evaluate("JSON.parse(localStorage.getItem('ada-layer-look'))"))["t:map"][
                "opacity"
            ] == 0.5
            await panel.get_by_label("Opacity of Map").fill("1")
            await page.wait_for_timeout(500)
            assert (await _seen(page))["magenta"] > 0.8 * last["magenta"]

            # ---- released with the plugin's overlays: layers, rows, track
            await page.evaluate(f'{SCENE}.remove("t"); {SCENE}.remove("scripted")')
            await page.wait_for_timeout(700)
            gone = await _seen(page)
            assert gone["magenta"] == gone["cyan"] == gone["yellow"] == 0, f"{backend}: {gone}"
            assert await layers_button.count() == 0, f"{backend}: the Layers button outlived the layers"
            assert (await _playback(page))["tracks"] == 1

            # ---- the orbit pivot: a double-click on the block makes it what the camera turns about
            await _seek(page, 0)
            block = (await _seen(page))["block"]
            await _orbit(page, 160)
            swung = _distance(block, (await _seen(page))["block"])
            assert swung > 40, f"{backend}: an orbit about the model's middle moved the block only {swung:.0f} px"
            await _orbit(page, -160)
            block = (await _seen(page))["block"]
            await page.mouse.dblclick(*block)
            await page.wait_for_timeout(1200)
            # The first of the two clicks selected the block: a click on the background lets go of it.
            await _click(page, 60, VIEWPORT["height"] - 60)
            pivot = (await _seen(page))["block"]
            assert pivot, f"{backend}: the block is not on screen after the double-click"
            await _orbit(page, 160)
            held = _distance(pivot, (await _seen(page))["block"])
            assert held < 0.45 * swung, f"{backend}: orbiting about the block moved it {held:.0f} px (was {swung:.0f})"
            await _assert_drawing_with(page, backend, state)
        finally:
            await browser.close()


@pytest.mark.parametrize("backend", WAYS)
async def test_a_draco_compressed_model_loads_from_the_bundle_alone(ws_server, backend):
    """A GLB whose mesh is Draco-compressed is decoded by what the viewer's bundle carries (three.js'
    decoder, or the renderer's own reader): the page asks no other host for anything."""
    async with async_playwright() as playwright:
        browser, page, _ = await _open_viewer(playwright, ws_server, backend, SCRIPTED_SCENE)
        try:
            elsewhere: list[str] = []
            page.on(
                "request",
                lambda r: None if r.url.startswith(("http://localhost", "blob:", "data:")) else elsewhere.append(r.url),
            )
            before = np.asarray(await _pixels(page)).astype(np.int32)
            await _load_model(page, base64.b64decode(DRACO_CUBE_GLB), "cube.glb")

            async def drawn():
                now = np.asarray(await _pixels(page)).astype(np.int32)
                # The cube where the empty viewport was: in the middle of the view, clear of the toolbars.
                changed = np.abs(now - before).max(axis=2)[150:700, 300:1000] > 40
                return int(changed.sum()) > 5000

            await _until(page, drawn, f"{backend}: the Draco-compressed cube to be drawn")
            assert not elsewhere, f"the page fetched from outside the bundle: {elsewhere}"
        finally:
            await browser.close()


# An image larger than a grid of cells, with pixels that are clear, half clear and opaque.
IMAGE_SIDE = 1024
ADD_IMAGE = f"""() => {{
    const S = {SCENE};
    const N = {IMAGE_SIDE};
    const pixels = new Uint8Array(N * N * 4);
    // `keep(column, row)` says how opaque a pixel is; every pixel is magenta.
    const paint = (alpha) => {{
        for (let j = 0, p = 0; j < N; j++) for (let i = 0; i < N; i++, p += 4) {{
            pixels[p] = pixels[p + 2] = 255;
            pixels[p + 1] = 0;
            pixels[p + 3] = alpha(i, j);
        }}
        return pixels;
    }};
    const image = S.addImage("t", {{origin: [0, 0, 0.02], u: [4, 0, 0], v: [0, 2, 0], width: N, height: N, pixels: paint(() => 255), nearest: true}});
    window.__image = {{image, paint, N}};
    return image.unavailable();
}}"""


async def _marker_at(page: Page, x: float, y: float) -> tuple[float, float]:
    """Where a point of the model's own coordinates is on screen."""
    await page.evaluate(
        f"""window.__marker = {SCENE}.addPointLayer("m", {{
            positions: [{x}, {y}, 0.05], color: "#ff0000", sizePx: 16, throughModel: true}})"""
    )
    await page.wait_for_timeout(500)
    at = (await _seen(page))["marker"]
    await page.evaluate(f'{SCENE}.remove("m")')
    await page.wait_for_timeout(300)
    assert at, "the marker is not drawn"
    return at


@pytest.mark.parametrize("backend", WAYS)
async def test_an_image_in_the_world_has_its_first_row_at_its_origin_and_each_pixels_own_alpha(ws_server, backend):
    """A large image on the ground: its rows and columns lie where the rectangle says, a clear pixel
    shows what is under it, a half-clear one is blended, and new pixels replace the old in place."""
    async with async_playwright() as playwright:
        browser, page, state = await _open_viewer(playwright, ws_server, backend, SCRIPTED_SCENE)
        try:
            await _load_model(page, _animated_glb(), "sliding.glb")
            await _until(page, lambda: _block_on_screen(page), "the model to be drawn")
            await _assert_drawing_with(page, backend, state)
            await page.wait_for_timeout(500)
            near_origin = await _marker_at(page, 0.5, 0.5)
            far_row = await _marker_at(page, 0.5, 1.5)
            far_column = await _marker_at(page, 3.5, 0.5)

            assert await page.evaluate(ADD_IMAGE) is None, f"{backend}: a {IMAGE_SIDE} x {IMAGE_SIDE} image is refused"
            await page.wait_for_timeout(800)
            whole = await _seen(page)
            assert whole["magenta"] > 20000, f"{backend}: the image is not drawn: {whole}"

            async def painted(alpha: str) -> dict:
                await page.evaluate(f"window.__image.image.setPixels(window.__image.paint({alpha}))")
                await page.wait_for_timeout(700)
                return await _seen(page)

            # the first rows only: the half of the rectangle at its origin, along u
            rows = await painted("(i, j) => (j < window.__image.N / 2 ? 255 : 0)")
            assert 0.3 * whole["magenta"] < rows["magenta"] < 0.7 * whole["magenta"], f"{backend}: {rows} of {whole}"
            assert _distance(rows["image"], near_origin) < _distance(
                rows["image"], far_row
            ), f"{backend}: the first rows are not at the origin: image {rows['image']}, origin {near_origin}, far {far_row}"

            # the first columns only: the half at the origin, along v
            columns = await painted("(i, j) => (i < window.__image.N / 2 ? 255 : 0)")
            assert 0.3 * whole["magenta"] < columns["magenta"] < 0.7 * whole["magenta"], f"{backend}: {columns}"
            assert _distance(columns["image"], near_origin) < _distance(
                columns["image"], far_column
            ), f"{backend}: the first columns are not at the origin: {columns['image']}, {near_origin}, {far_column}"

            # half-clear pixels are blended with what is under them: no longer the pure colour
            blended = await painted("() => 128")
            assert (
                blended["magenta"] < 0.05 * whole["magenta"] + 20
            ), f"{backend}: half-clear pixels are opaque: {blended}"
            rgb = np.asarray(await _pixels(page)).astype(np.int32)
            pinkish = (rgb[..., 0] > rgb[..., 1] + 40) & (rgb[..., 2] > rgb[..., 1] + 40)
            assert int(pinkish.sum()) > 0.5 * whole["magenta"], f"{backend}: half-clear pixels are not drawn at all"

            # and opaque again
            again = await painted("() => 255")
            assert again["magenta"] > 0.9 * whole["magenta"]
            await page.evaluate(f'{SCENE}.remove("t")')
            await page.wait_for_timeout(500)
            assert (await _seen(page))["magenta"] == 0
        finally:
            await browser.close()

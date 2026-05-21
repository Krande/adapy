from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Optional

import numpy as np
import pygfx as gfx
import trimesh
from PIL import Image

# wgpu 0.20+ split the GUI shims out into the standalone `rendercanvas`
# package; the old `wgpu.gui.offscreen` import path was removed entirely
# by 0.31. Prefer the new location, fall back so installations still on
# wgpu < 0.20 keep working.
try:
    from rendercanvas.offscreen import OffscreenRenderCanvas as _OffscreenCanvas
except ImportError:  # pragma: no cover - exercised only on legacy wgpu
    from wgpu.gui.offscreen import WgpuCanvas as _OffscreenCanvas  # type: ignore[no-redef]

import ada
from ada.visit.rendering.camera import Camera


def _read_glb_primitives(glb_path: str | Path) -> list[dict]:
    """Parse a binary GLB and return its primitives raw.

    Returns one dict per primitive across all meshes:
      {
        "mode": int,                      # 4=TRIANGLES, 1=LINES, …
        "positions": np.ndarray (N, 3),   # float32
        "indices":   np.ndarray (M,) | None,  # int32, when an index buffer exists
        "colors":    np.ndarray (N, 4) | None,  # float32, when COLOR_0 exists
      }

    Bypasses trimesh on purpose. trimesh.path.Path3D collapses every
    LINES primitive into a single polyline-shaped entity with
    consecutive indices [0,1,2,3,…], which makes any consumer that
    interprets it as LINE_STRIP draw the wrong topology — the FEA
    edge-overlay "spaghetti wireframe" bug. Reading the GLB ourselves
    keeps the offscreen renderer on the same side of the contract as
    Three.js' GLTFLoader (which the embedded viewer uses).
    """
    p = Path(glb_path)
    raw = p.read_bytes()
    if raw[:4] != b"glTF":
        raise ValueError(f"{p}: not a binary GLB (missing 'glTF' magic)")
    _magic, _version, _total = struct.unpack("<III", raw[:12])
    json_len, json_type = struct.unpack("<II", raw[12:20])
    if json_type != 0x4E4F534A:  # 'JSON'
        raise ValueError(f"{p}: expected JSON chunk header, got 0x{json_type:08x}")
    json_bytes = raw[20 : 20 + json_len]
    bin_offset = 20 + json_len + 8  # +8 skips BIN chunk header (length, type)
    bin_bytes = raw[bin_offset:]
    gltf = json.loads(json_bytes)

    accessors = gltf.get("accessors", [])
    buffer_views = gltf.get("bufferViews", [])

    # componentType (GLTF spec) → numpy dtype string. Multi-buffer GLBs
    # are rare and adapy never writes them, but we still resolve via
    # `buffer` index to be defensive.
    dtype_map = {5120: "i1", 5121: "u1", 5122: "i2", 5123: "u2", 5125: "u4", 5126: "f4"}
    elem_map = {
        "SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
        "MAT2": 4, "MAT3": 9, "MAT4": 16,
    }

    def read_accessor(idx: int) -> np.ndarray:
        a = accessors[idx]
        bv = buffer_views[a["bufferView"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        count = a["count"]
        elem = elem_map[a["type"]]
        dtype = np.dtype("<" + dtype_map[a["componentType"]])
        arr = np.frombuffer(bin_bytes, dtype=dtype, count=count * elem, offset=off)
        if elem > 1:
            arr = arr.reshape((count, elem))
        return arr

    out: list[dict] = []
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "POSITION" not in attrs:
                continue
            positions = read_accessor(attrs["POSITION"]).astype(np.float32, copy=False)
            indices = (
                read_accessor(prim["indices"]).astype(np.int32, copy=False)
                if "indices" in prim
                else None
            )
            colors = (
                read_accessor(attrs["COLOR_0"]).astype(np.float32, copy=False)
                if "COLOR_0" in attrs
                else None
            )
            out.append(
                {
                    "mode": prim.get("mode", 4),  # default = TRIANGLES per spec
                    "positions": positions,
                    "indices": indices,
                    "colors": colors,
                }
            )
    return out


def _apply_embed_preset_camera(
    positions_all: np.ndarray,
    aspect: float,
    *,
    azimuth_deg: float,
    elevation_deg: float,
    fov_deg: float,
    distance: float | str,
    margin: float,
    z_up: bool,
) -> gfx.PerspectiveCamera:
    """Frame a pygfx camera the way the embedded viewer would.

    Verbatim port of ``applyCameraPreset`` in
    ``adapy/src/frontend/embed/index.ts`` (and the
    ``setLookAt(position, target, ...)`` call that follows) so the
    poster's initial pose is bit-identical to what the user sees the
    first time the live 3D viewer mounts the same GLB.
    """
    if positions_all.size == 0:
        # Empty scene — fall back to a safe identity-ish setup.
        cam = gfx.PerspectiveCamera(fov_deg, max(aspect, 1e-3), depth_range=(0.01, 100.0))
        cam.local.up = (0.0, 0.0, 1.0) if z_up else (0.0, 1.0, 0.0)
        return cam

    mn = positions_all.min(axis=0)
    mx = positions_all.max(axis=0)
    center = (mn + mx) * 0.5
    size = mx - mn
    radius = max(float(np.linalg.norm(size) * 0.5), 1e-3)

    fov_rad = math.radians(fov_deg)
    if isinstance(distance, (int, float)) and not isinstance(distance, bool):
        d = float(distance)
    else:
        fit_v = radius / math.sin(fov_rad / 2)
        fit_h = radius / math.sin(math.atan(math.tan(fov_rad / 2) * max(aspect, 1e-6)))
        d = max(fit_v, fit_h) * margin

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    if z_up:
        offset = np.array([
            d * math.cos(el) * math.sin(az),
            d * math.cos(el) * math.cos(az),
            d * math.sin(el),
        ], dtype=np.float64)
        up = (0.0, 0.0, 1.0)
    else:
        offset = np.array([
            d * math.cos(el) * math.sin(az),
            d * math.sin(el),
            d * math.cos(el) * math.cos(az),
        ], dtype=np.float64)
        up = (0.0, 1.0, 0.0)
    position = center + offset

    near = max(d / 1000.0, 1e-3)
    far = d * 100.0

    cam = gfx.PerspectiveCamera(fov_deg, max(aspect, 1e-3), depth_range=(near, far))
    cam.local.up = up
    cam.local.position = tuple(position.tolist())
    cam.look_at(tuple(center.tolist()))
    return cam


def glb_to_image(
    glb_path: str | Path,
    *,
    # Camera preset args mirror `applyCameraPreset` in adapy's embed
    # viewer one-to-one. Defaults match the `iso_3` preset that
    # paradoc's CAD / FEA figures use when no explicit `camera_pos`
    # is supplied. Same math, same field-of-view → live viewer and
    # poster line up to the pixel.
    azimuth_deg: float = -135.0,
    elevation_deg: float = 30.0,
    fov_deg: float = 45.0,
    distance: float | str = "fit",
    margin: float = 0.1,
    z_up: bool = True,
    size: tuple[int, int] = (640, 480),
    line_color: tuple[float, float, float, float] = (0.15, 0.15, 0.15, 1.0),
    line_thickness: float = 1.5,
) -> Image.Image:
    """Render ``glb_path`` to a PIL Image with full GLTF mode fidelity.

    Reads the GLB directly (see ``_read_glb_primitives``) so LINES
    primitives stay LINES, and frames the camera the same way the
    embedded Three.js viewer does — preset-driven, identical math.
    """
    canvas = _OffscreenCanvas(size=size, pixel_ratio=1)
    renderer = gfx.renderers.WgpuRenderer(canvas)
    scene = gfx.Scene()
    group = scene.add(gfx.Group())

    drew_any = False
    all_positions: list[np.ndarray] = []
    for prim in _read_glb_primitives(glb_path):
        mode = prim["mode"]
        positions = prim["positions"]
        indices = prim["indices"]
        colors = prim["colors"]
        all_positions.append(positions)

        if mode == 4:  # TRIANGLES
            geom_kwargs = {"positions": positions}
            if indices is not None:
                # pygfx wants index triples in (M, 3) shape.
                geom_kwargs["indices"] = indices.reshape(-1, 3)
            if colors is not None:
                geom_kwargs["colors"] = colors
            try:
                geometry = gfx.Geometry(**geom_kwargs)
                material = (
                    gfx.MeshPhongMaterial(color_mode="vertex")
                    if colors is not None
                    else gfx.MeshPhongMaterial()
                )
                group.add(gfx.Mesh(geometry, material))
                drew_any = True
            except Exception:
                # Swallow per-primitive failures so a bad mesh doesn't
                # blank the whole poster.
                continue
        elif mode == 1:  # LINES — stride-2 segment pairs
            if indices is not None:
                line_positions = positions[indices].astype(np.float32, copy=False)
            else:
                line_positions = positions
            # Even count required for LineSegments. Trim one off if the
            # writer emitted an odd vertex count.
            if len(line_positions) % 2 != 0:
                line_positions = line_positions[:-1]
            if len(line_positions) < 2:
                continue
            try:
                geometry = gfx.Geometry(positions=line_positions.reshape(-1, 3))
                material = gfx.LineSegmentMaterial(
                    color=line_color, thickness=line_thickness
                )
                group.add(gfx.Line(geometry, material))
                drew_any = True
            except Exception:
                continue
        # Other GLTF modes (POINTS, LINE_LOOP, LINE_STRIP, TRIANGLE_STRIP,
        # TRIANGLE_FAN) are not emitted by adapy's writer today. Skip
        # silently; add support here when the writer grows them.

    if not drew_any:
        # Render an empty scene rather than crash; callers can still
        # show the resulting blank image as a fallback poster.
        pass

    scene.add(gfx.AmbientLight(intensity=0.6))
    scene.add(gfx.DirectionalLight())

    width, height = canvas.get_logical_size()
    aspect = (width / height) if height else 4.0 / 3.0
    positions_all = (
        np.concatenate(all_positions, axis=0) if all_positions else np.zeros((0, 3), dtype=np.float32)
    )
    gfx_cam = _apply_embed_preset_camera(
        positions_all,
        aspect,
        azimuth_deg=azimuth_deg,
        elevation_deg=elevation_deg,
        fov_deg=fov_deg,
        distance=distance,
        margin=margin,
        z_up=z_up,
    )

    scene.add(gfx_cam)
    canvas.request_draw(lambda: renderer.render(scene, gfx_cam))
    img_array = canvas.draw()
    return Image.fromarray(np.asarray(img_array))


def screenshot(part: ada.Part, filename: str, camera: Optional[Camera] = None):
    tri_scene = part.to_trimesh_scene()
    image = trimesh_scene_to_image(tri_scene, camera=camera)
    # Save the image to a file
    image.save(filename)


def trimesh_scene_to_image(tri_scene: trimesh.Scene, camera: Optional[Camera] = None) -> Image.Image:
    canvas = _OffscreenCanvas(size=(640, 480), pixel_ratio=1)
    renderer = gfx.renderers.WgpuRenderer(canvas)

    scene = gfx.Scene()
    geom = scene.add(gfx.Group())
    meshes = []
    for mesh in tri_scene.geometry.values():
        meshes.append(
            gfx.Mesh(
                gfx.geometry_from_trimesh(mesh),
                gfx.MeshPhongMaterial(),
            )
        )
    geom.add(*meshes)
    dir_light = gfx.DirectionalLight()
    scene.add(gfx.AmbientLight(intensity=0.5))

    camera_obj = camera or Camera()

    width, height = canvas.get_logical_size()
    gfx_camera = gfx.PerspectiveCamera(camera_obj.fov, width / height, depth_range=(camera_obj.near, camera_obj.far))

    if camera_obj.fit_view:
        view_dir = (-1, -1, -1)
        if camera_obj.position is not None and camera_obj.look_at is not None:
            view_dir = np.array(camera_obj.look_at) - np.array(camera_obj.position)

        gfx_camera.show_object(geom, view_dir=view_dir, up=camera_obj.up or (0, 0, 1))

        if camera_obj.padding > 0:
            # Adjust zoom for padding. 0.8 zoom means 80% filling.
            gfx_camera.zoom = 1 - camera_obj.padding
    else:
        if camera_obj.up is not None:
            gfx_camera.local.up = camera_obj.up
        if camera_obj.position is not None:
            gfx_camera.local.position = camera_obj.position
        if camera_obj.look_at is not None:
            gfx_camera.look_at(camera_obj.look_at)

    scene.add(gfx_camera)
    scene.add(dir_light)
    canvas.request_draw(lambda: renderer.render(scene, gfx_camera))
    im1 = canvas.draw()
    return Image.fromarray(np.asarray(im1))

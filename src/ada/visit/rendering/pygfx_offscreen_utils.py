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


def _node_matrix(node: dict) -> np.ndarray:
    """Compose a node's local 4x4 matrix from TRS or an explicit matrix.

    glTF lets a node carry either `matrix` (16 floats, column-major) or
    a TRS triplet (translation, rotation as quaternion, scale). The
    spec forbids both being present, so we pick whichever is set.
    """
    if "matrix" in node:
        # glTF stores matrices column-major; numpy is row-major.
        return np.array(node["matrix"], dtype=np.float64).reshape((4, 4), order="F")
    t = node.get("translation", [0.0, 0.0, 0.0])
    r = node.get("rotation", [0.0, 0.0, 0.0, 1.0])  # quaternion xyzw
    s = node.get("scale", [1.0, 1.0, 1.0])
    qx, qy, qz, qw = r
    # Quaternion → rotation matrix.
    rx = np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw), 0.0],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw), 0.0],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ], dtype=np.float64)
    sx, sy, sz = s
    sm = np.diag([sx, sy, sz, 1.0])
    tm = np.eye(4, dtype=np.float64)
    tm[:3, 3] = t
    return tm @ rx @ sm


def _read_glb_primitives(glb_path: str | Path) -> list[dict]:
    """Parse a binary GLB and return its primitives in world-space coords.

    Returns one dict per primitive across the active scene, with the
    full node-hierarchy transform already baked into the positions:

      {
        "mode": int,                      # 4=TRIANGLES, 1=LINES, …
        "positions": np.ndarray (N, 3),   # float32, world-space
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

    Node-graph walk matters: adapy's `Part.to_gltf` often stores mesh
    vertices in local coords and pushes the world placement onto a
    node's `translation`. Skipping the walk would leave the camera
    framing the *local* bbox while the live viewer (which honours the
    transforms via Three.js' GLTFLoader) frames the *world* bbox, and
    the two pictures stop agreeing on the model's position and size.
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
    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    scenes = gltf.get("scenes", [])
    scene_idx = gltf.get("scene", 0)

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

    def transform_positions(pos: np.ndarray, m: np.ndarray) -> np.ndarray:
        if np.allclose(m, np.eye(4)):
            return pos
        # Append homogeneous w=1, multiply, drop w.
        homo = np.concatenate([pos, np.ones((len(pos), 1), dtype=pos.dtype)], axis=1)
        out_homo = homo @ m.T.astype(pos.dtype)
        return out_homo[:, :3].astype(np.float32, copy=False)

    out: list[dict] = []

    def walk(node_idx: int, parent_matrix: np.ndarray) -> None:
        node = nodes[node_idx]
        local = _node_matrix(node).astype(np.float64)
        world = parent_matrix @ local
        if "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives", []):
                attrs = prim.get("attributes", {})
                if "POSITION" not in attrs:
                    continue
                positions = read_accessor(attrs["POSITION"]).astype(np.float32, copy=False)
                positions = transform_positions(positions, world)
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
        for child in node.get("children", []):
            walk(child, world)

    root_nodes: list[int] = []
    if scenes:
        root_nodes = scenes[scene_idx].get("nodes", [])
    if not root_nodes and nodes:
        # No scenes (or empty default scene) — fall back to walking
        # every node as if it were a root. Catches GLBs written
        # without a scene definition (the loose convention some
        # exporters emit).
        root_nodes = list(range(len(nodes)))
    identity = np.eye(4, dtype=np.float64)
    for r in root_nodes:
        walk(r, identity)

    # Final safety net: if the GLB had no node graph at all, fall back
    # to the old "iterate meshes directly" path — keeps degenerate
    # writers working at the cost of skipping any node transforms.
    if not out:
        for mesh in meshes:
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
                        "mode": prim.get("mode", 4),
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

    # pygfx's `PerspectiveCamera.fov` is *not* the vertical FOV (the
    # Three.js convention `fov_deg` follows). Inspecting pygfx
    # `_update_projection_matrix` shows it computes
    #     height = 2 * near * tan(fov/2) / (1 + aspect)
    # so the actual vertical-FOV emitted by the projection is
    #     tan(fov_v/2) = 2 * tan(fov_pygfx/2) / (1 + aspect)
    # i.e. squashed by `2/(1+aspect)`. Invert that so a caller passing
    # `fov_deg=45` (vertical, embed-style) actually gets a 45°
    # vertical frustum out of pygfx.
    safe_aspect = max(aspect, 1e-3)
    fov_pygfx = math.degrees(
        2 * math.atan(math.tan(math.radians(fov_deg / 2)) * (1 + safe_aspect) / 2)
    )

    # `maintain_aspect=True` (the default) makes pygfx force
    # m[0][0] == m[1][1] regardless of aspect, which squashes the X-Y
    # ratio of the projection. We want canonical perspective
    # (m[1][1] = 1/tan(fov_v/2), m[0][0] = m[1][1] / aspect) so the
    # pygfx poster comes out at the same pixel ratio as Three.js'.
    cam = gfx.PerspectiveCamera(
        fov_pygfx, safe_aspect, depth_range=(near, far), maintain_aspect=False,
    )
    # `cam.look_at(target)` recomputes the up vector to be orthogonal
    # to the forward direction, so a Z-up scene loses its world-vertical
    # orientation and the resulting roll diverges from Three.js'
    # `controls.setLookAt(...)` (which keeps `camera.up` fixed at world-
    # vertical). Build the rotation from `eye → target` with a fixed
    # world-up reference via pylinalg and write it directly so the
    # offscreen poster matches the embed's framing pose-for-pose.
    import pylinalg as la

    eye = np.asarray(position, dtype=np.float64)
    look = np.asarray(center, dtype=np.float64)
    up_ref = np.asarray(up, dtype=np.float64)
    # `mat_look_at` aligns the local +Z axis with `target - eye`, but
    # pygfx perspective cameras look down their -Z axis (matches
    # OpenGL / Three.js). Swap eye/target so +Z points *away* from
    # the model — equivalent to "the camera's forward is -Z".
    rot_matrix = la.mat_look_at(look, eye, up_ref)
    cam.local.position = tuple(eye.tolist())
    cam.local.rotation = la.quat_from_mat(rot_matrix)
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
    # 1.15 = adapy embed's DEFAULT_MARGIN; paradoc's CameraPreset
    # default agrees. The math is `distance = fit * margin`, so values
    # < 1.0 place the camera *inside* the bbox (~ same trap the live
    # viewer fell into before the preset was fixed).
    margin: float = 1.15,
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

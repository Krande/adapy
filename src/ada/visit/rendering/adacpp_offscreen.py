"""Offscreen GLB -> image through adacpp's WebGPU renderer (``adacpp._render``, wgpu-native).

The renderer Ada Studio draws with in its WebGPU mode, run headless: same clusters, same shading (three.js'
MeshStandardMaterial port), same lights. It replaces the pygfx path (``pygfx_offscreen_utils``) with one
small native extension instead of the pygfx/wgpu-py/imgui closure.

The camera is the embed's ``applyCameraPreset`` (adapy ``src/frontend/embed/index.ts``), as in the pygfx
port, so posters frame the model the way the live viewer first shows it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from PIL.Image import Image


def is_available() -> bool:
    """True when the installed adacpp ships the renderer extension and a WebGPU adapter can be loaded."""
    try:
        from adacpp import _render  # noqa: F401
    except ImportError:
        return False
    return True


def preset_camera(
    bounds: tuple[float, float, float, float, float, float] | np.ndarray,
    aspect: float,
    *,
    azimuth_deg: float = -135.0,
    elevation_deg: float = 30.0,
    fov_deg: float = 45.0,
    distance: float | str = "fit",
    margin: float = 1.15,
    z_up: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """View and projection matrices (column-major, flattened; OpenGL clip space as three.js builds it) for
    the embed's camera preset around ``bounds`` (min xyz, max xyz).

    ``distance="fit"`` backs off until the bounding sphere fits the narrower field of view, times
    ``margin`` (the embed's ``DEFAULT_MARGIN`` is 1.15).
    """
    b = np.asarray(bounds, dtype=np.float64)
    mn, mx = b[:3], b[3:]
    center = (mn + mx) * 0.5
    radius = max(float(np.linalg.norm(mx - mn) * 0.5), 1e-3)
    aspect = max(aspect, 1e-3)

    fov_rad = math.radians(fov_deg)
    if isinstance(distance, (int, float)) and not isinstance(distance, bool):
        d = float(distance)
    else:
        fit_v = radius / math.sin(fov_rad / 2)
        fit_h = radius / math.sin(math.atan(math.tan(fov_rad / 2) * aspect))
        d = max(fit_v, fit_h) * margin

    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    if z_up:
        offset = np.array([d * math.cos(el) * math.sin(az), d * math.cos(el) * math.cos(az), d * math.sin(el)])
        up = np.array([0.0, 0.0, 1.0])
    else:
        offset = np.array([d * math.cos(el) * math.sin(az), d * math.sin(el), d * math.cos(el) * math.cos(az)])
        up = np.array([0.0, 1.0, 0.0])
    eye = center + offset
    near, far = max(d / 1000.0, 1e-3), d * 100.0

    # three.js Matrix4.lookAt + camera.up fixed at world vertical (what controls.setLookAt keeps).
    f = center - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-9:  # looking straight along the up axis
        s = np.cross(f, np.array([0.0, 1.0, 0.0]) if z_up else np.array([0.0, 0.0, 1.0]))
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    view = np.eye(4)
    view[0, :3], view[1, :3], view[2, :3] = s, u, -f
    view[:3, 3] = -view[:3, :3] @ eye

    t = 1.0 / math.tan(fov_rad / 2)
    proj = np.zeros((4, 4))
    proj[0, 0] = t / aspect
    proj[1, 1] = t
    proj[2, 2] = (far + near) / (near - far)
    proj[2, 3] = 2 * far * near / (near - far)
    proj[3, 2] = -1.0
    # Column-major, as three.js' Matrix4.elements.
    return (
        np.ascontiguousarray(view.T, dtype=np.float32).ravel(),
        np.ascontiguousarray(proj.T, dtype=np.float32).ravel(),
    )


def glb_to_image(
    glb_path: str | Path,
    *,
    azimuth_deg: float = -135.0,
    elevation_deg: float = 30.0,
    fov_deg: float = 45.0,
    distance: float | str = "fit",
    margin: float = 1.15,
    z_up: bool = True,
    size: tuple[int, int] = (640, 480),
    line_color: tuple[float, float, float, float] = (0.15, 0.15, 0.15, 1.0),
    line_thickness: float = 1.5,
    member_thickness: float = 4.0,
    point_size: float = 4.0,
    background: tuple[float, float, float] | None = None,
    overlay: dict | None = None,
) -> "Image":
    """Render ``glb_path`` to a PIL Image; the signature of ``pygfx_offscreen_utils.glb_to_image``.

    Lines without vertex colours (element / feature edges) draw in ``line_color`` (sRGB, as pygfx reads
    colours) at ``line_thickness`` pixels; coloured lines (beam members) in their own colours at
    ``member_thickness``. ``background``: sRGB, opaque; ``None`` = transparent, as the pygfx posters are.

    ``overlay`` draws Ada Studio's orientation gizmo and colour legend over the image, e.g.
    ``{"gizmo": True, "legend": {"header": [...], "gradient": [[r, g, b], ...], "ticks": [...]}}``
    (sRGB colours; the spec in full: adacpp ``src/render/overlay_json.h``).
    """
    from adacpp import _render
    from PIL import Image

    width, height = size
    renderer = _render.OffscreenRenderer(width, height)
    renderer.load_glb(
        str(glb_path),
        line_width=line_thickness,
        member_width=member_thickness,
        point_size=point_size,
        line_color=(*(_srgb_to_linear(float(c)) for c in line_color[:3]), float(line_color[3])),
        # pygfx draws both faces by default and shell posters rely on it: an FEA shell seen from behind.
        double_sided=True,
    )
    if background is None:
        renderer.set_background(0.0, 0.0, 0.0, 0.0)
    else:
        renderer.set_background(*background, 1.0)
    view, proj = preset_camera(
        renderer.bounds(),
        width / height if height else 4.0 / 3.0,
        azimuth_deg=azimuth_deg,
        elevation_deg=elevation_deg,
        fov_deg=fov_deg,
        distance=distance,
        margin=margin,
        z_up=z_up,
    )
    renderer.set_camera(view, proj)
    if overlay:
        legend = overlay.get("legend")
        if background is None and isinstance(legend, dict) and "panel_bg" not in legend:
            # Studio's panel is 62% slate over the dark viewport; over a transparent poster on a white
            # page that turns grey, so make it nearly opaque to read the same anywhere.
            overlay = {**overlay, "legend": {**legend, "panel_bg": [30 / 255, 41 / 255, 59 / 255, 0.92]}}
        renderer.set_overlay(json.dumps(overlay))
    rgba = renderer.render()
    if background is None:
        rgba = _unpremultiply(rgba)
    return Image.fromarray(rgba)


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _unpremultiply(rgba: np.ndarray) -> np.ndarray:
    """Multisample resolve against a transparent background leaves edge pixels premultiplied (colour x
    coverage); a PNG stores straight alpha, so divide the coverage back out or edges darken."""
    alpha = rgba[..., 3:4].astype(np.float32)
    edge = (alpha > 0) & (alpha < 255)
    rgb = rgba[..., :3].astype(np.float32)
    rgb = np.where(edge, np.clip(rgb * 255.0 / np.maximum(alpha, 1.0), 0, 255), rgb)
    out = rgba.copy()
    out[..., :3] = rgb.astype(np.uint8)
    return out

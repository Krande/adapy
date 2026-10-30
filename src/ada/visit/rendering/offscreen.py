"""Offscreen GLB -> image: one entry point over the offscreen renderers.

- ``"adacpp"``: adacpp's WebGPU renderer, headless (``adacpp._render``) -- the renderer Ada Studio's
  WebGPU mode draws with.
- ``"pygfx"``: the pygfx renderer this replaces; kept while adacpp builds without the extension are about.
- ``"chromium"``: the production embed in headless Chromium (Playwright); the visual oracle for tests.
- ``"auto"``: adacpp when its extension is installed, else pygfx.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from PIL.Image import Image

OffscreenBackend = Literal["auto", "adacpp", "pygfx", "chromium"]

# The camera preset fields every backend honours (paradoc's CameraPreset uses the same names).
PRESET_KEYS = ("azimuth_deg", "elevation_deg", "fov_deg", "distance", "margin", "z_up")


def resolve_backend(backend: OffscreenBackend) -> Literal["adacpp", "pygfx", "chromium"]:
    if backend != "auto":
        return backend
    from ada.visit.rendering.adacpp_offscreen import is_available

    return "adacpp" if is_available() else "pygfx"


def glb_to_image(
    glb_path: str | Path,
    *,
    backend: OffscreenBackend = "auto",
    preset: dict | None = None,
    size: tuple[int, int] = (640, 480),
    overlay: dict | None = None,
) -> "Image":
    """Render ``glb_path`` with ``backend``, framed by the embed's camera ``preset`` (keys: PRESET_KEYS).

    ``overlay``: Ada Studio's orientation gizmo / colour legend over the image (adacpp's overlay spec,
    see ``adacpp_offscreen.glb_to_image``); drawn by the adacpp backend only.
    """
    resolved = resolve_backend(backend)
    if resolved == "chromium":
        from ada.visit.rendering.chromium_offscreen_utils import (
            glb_to_image_via_browser,
        )

        return glb_to_image_via_browser(glb_path, preset=preset, size=size)
    kwargs = {k: v for k, v in (preset or {}).items() if k in PRESET_KEYS}
    if resolved == "adacpp":
        from ada.visit.rendering.adacpp_offscreen import glb_to_image as render_adacpp

        return render_adacpp(glb_path, size=size, overlay=overlay, **kwargs)
    from ada.visit.rendering.pygfx_offscreen_utils import glb_to_image as render_pygfx

    return render_pygfx(glb_path, size=size, **kwargs)

"""Offscreen GLB -> image: one entry point over the offscreen renderers.

- ``"adacpp"``: adacpp's WebGPU renderer, headless (``adacpp._render``) -- the renderer Ada Studio's
  WebGPU mode draws with.
- ``"chromium"``: the production embed in headless Chromium (Playwright); the visual oracle for tests.
- ``"auto"``: adacpp.

``"pygfx"``, the renderer adacpp replaced, is still accepted and renders with adacpp, with a
DeprecationWarning.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from PIL.Image import Image

OffscreenBackend = Literal["auto", "adacpp", "chromium"]

# The camera preset fields every backend honours (paradoc's CameraPreset uses the same names).
PRESET_KEYS = ("azimuth_deg", "elevation_deg", "fov_deg", "distance", "margin", "z_up")


def resolve_backend(backend: OffscreenBackend) -> Literal["adacpp", "chromium"]:
    if backend == "pygfx":
        warnings.warn(
            'The "pygfx" offscreen backend is removed; rendering with "adacpp" (adacpp\'s WebGPU renderer). '
            'Pass backend="auto" or "adacpp".',
            DeprecationWarning,
            stacklevel=3,
        )
        return "adacpp"
    if backend == "auto":
        return "adacpp"
    return backend


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
    from ada.visit.rendering.adacpp_offscreen import glb_to_image as render_adacpp

    kwargs = {k: v for k, v in (preset or {}).items() if k in PRESET_KEYS}
    return render_adacpp(glb_path, size=size, overlay=overlay, **kwargs)

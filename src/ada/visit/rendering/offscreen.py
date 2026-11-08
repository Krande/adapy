"""Offscreen GLB -> image: one entry point over the offscreen renderers.

- ``"adacpp"``: adacpp's WebGPU renderer, headless (``adacpp._render``) -- the renderer Ada Studio's
  WebGPU mode draws with.
- ``"chromium"``: the production embed in headless Chromium (Playwright); the visual oracle for tests.
- ``"auto"``: adacpp; chromium when the installed adacpp has no renderer and chromium can run.

``"pygfx"``, the renderer adacpp replaced, is still accepted and renders with adacpp, with a
DeprecationWarning.

A backend that cannot run raises :class:`OffscreenUnavailableError`. Static images are the only
model views a PDF / DOCX export has, so callers that tolerate a failed render let this one through.
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


class OffscreenUnavailableError(RuntimeError):
    """No offscreen backend that was asked for (or, for ``"auto"``, none at all) can render here."""


def _adacpp_missing_message() -> str:
    try:
        from importlib.metadata import version

        installed = f"ada-cpp {version('ada-cpp')}"
    except Exception:  # noqa: BLE001 - not installed, or installed without metadata
        installed = "the installed ada-cpp"
    return (
        f"{installed} has no adacpp._render, the WebGPU renderer static model images are drawn with. "
        "Install an ada-cpp build that ships it."
    )


def resolve_backend(backend: OffscreenBackend) -> Literal["adacpp", "chromium"]:
    """The backend ``backend`` renders with; raises :class:`OffscreenUnavailableError` if it cannot run."""
    from ada.visit.rendering import adacpp_offscreen, chromium_offscreen_utils

    if backend == "pygfx":
        warnings.warn(
            'The "pygfx" offscreen backend is removed; rendering with "adacpp" (adacpp\'s WebGPU renderer). '
            'Pass backend="auto" or "adacpp".',
            DeprecationWarning,
            stacklevel=3,
        )
        backend = "adacpp"
    if backend == "auto":
        if adacpp_offscreen.is_available():
            return "adacpp"
        if chromium_offscreen_utils.is_available():
            warnings.warn(f"{_adacpp_missing_message()} Rendering with chromium instead.", stacklevel=3)
            return "chromium"
        raise OffscreenUnavailableError(
            f"No offscreen renderer: {_adacpp_missing_message()} The chromium backend needs playwright "
            "and a built embed (src/frontend/dist-embed/index.js)."
        )
    if backend == "adacpp" and not adacpp_offscreen.is_available():
        raise OffscreenUnavailableError(_adacpp_missing_message())
    return backend


def glb_to_image(
    glb_path: str | Path,
    *,
    backend: OffscreenBackend = "auto",
    preset: dict | None = None,
    size: tuple[int, int] = (640, 480),
    overlay: dict | None = None,
    edges: bool = False,
) -> "Image":
    """Render ``glb_path`` with ``backend``, framed by the embed's camera ``preset`` (keys: PRESET_KEYS).

    ``overlay``: Ada Studio's orientation gizmo / colour legend over the image (adacpp's overlay spec,
    see ``adacpp_offscreen.glb_to_image``); drawn by the adacpp backend only.
    ``edges``: draw the model's feature edges as Ada Studio's viewer does (boundary edges and
    edges between faces meeting at over 30 degrees, black; see ``feature_edges``). For CAD models;
    an FEA bundle's poster draws its own element edges.
    """
    resolved = resolve_backend(backend)
    if resolved == "chromium":
        from ada.visit.rendering.chromium_offscreen_utils import (
            glb_to_image_via_browser,
        )

        return glb_to_image_via_browser(glb_path, preset=preset, size=size)
    from ada.visit.rendering.adacpp_offscreen import glb_to_image as render_adacpp

    kwargs = {k: v for k, v in (preset or {}).items() if k in PRESET_KEYS}
    if not edges:
        return render_adacpp(glb_path, size=size, overlay=overlay, **kwargs)
    import tempfile

    from ada.visit.rendering.feature_edges import glb_with_feature_edges

    with tempfile.TemporaryDirectory(prefix="ada_edges_") as tmp:
        with_edges = glb_with_feature_edges(glb_path, Path(tmp) / "with_edges.glb")
        return render_adacpp(with_edges, size=size, overlay=overlay, **kwargs)

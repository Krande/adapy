"""Pyodide entrypoint: compile a procedural cell-model doc to GLB bytes.

This is the browser-side twin of the server ``procedural_build`` worker job — one
pure-python callable the Pyodide worker invokes on the already-installed adapy
wheel (mirroring :func:`ada.cadit.wasm_convert.run`). The built-in
``adapy-default`` procedural engine therefore runs entirely CLIENT-SIDE: geometry
is numpy + ``ada.geom``/``ada.topology`` and tessellation streams through the
adacpp/libtess2 wasm kernel (no OCC), exactly the OCC-free path
:func:`compile_procedural_doc` already takes for the server compile.

The browser has no database, so catalog-backed equipment (resolved server-side by
slug) and linked CAD assets are unavailable here: placed catalog equipment falls
back to the built-in archetypes, and unknown types render as placeholder boxes.
For catalog/CAD fidelity, compile server-side instead.
"""

from __future__ import annotations

import json
from typing import Literal

__all__ = ["compile_doc"]


def compile_doc(
    doc: str | dict,
    name: str = "ProceduralModel",
    blueprint_name: Literal["steel_stru", "none"] = "steel_stru",
    lod: Literal["sim", "detail"] = "sim",
) -> bytes:
    """Compile a procedural document to GLB bytes.

    ``doc`` is the cellbuilder commit document — the same
    ``{spaces, equipments, systems, openings, blueprint, design_rules}`` shape the
    server stores — as a dict or a JSON string (the Pyodide bridge hands over a
    string). ``lod`` picks the simulation (default) or detail level of detail.
    Returns the GLB bytes, ready to load straight into the viewer scene.
    """
    from ada.topo_model.compile import compile_procedural_doc

    parsed = json.loads(doc) if isinstance(doc, str) else dict(doc)
    return compile_procedural_doc(parsed, name=name, blueprint_name=blueprint_name, lod=lod)

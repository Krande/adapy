"""Per-line-element section + orientation sidecar for the viewer's
beam-solid bake.

Code Aster's .med output carries the mesh and result fields, but no
section profiles, no per-element material assignment, and no beam
orientation (``VECT_Y`` / ``local_z``) — those live in the .comm
deck that adapy generates alongside. The streaming-viewer bake needs
this metadata to tessellate beams as 3D extruded solids, mirroring
what the SIF / Abaqus paths get for free from their native formats.

This module dumps a ``<name>.beams.json`` sidecar next to the .med
at write time. The Code Aster solver round-trips the .med to .rmed
so the sidecar ends up sitting next to the .rmed result that the
viewer's bake worker opens; the RMED stream reader picks it up by
basename.

Schema (version 1):

.. code-block:: json

    {
      "version": 1,
      "units": "m",
      "beams": [
        {
          "elem_id": 17,
          "n0_id": 4, "n1_id": 5,
          "n0": [0.0, 0.0, 0.0], "n1": [1.0, 0.0, 0.0],
          "local_z": [0.0, 0.0, 1.0],
          "material_name": "S355",
          "section": {
            "name": "HP220x10",
            "type": "BG",
            "h": 0.22, "w_top": 0.10, "w_btn": null,
            "t_w": 0.01,
            "t_ftop": 0.012, "t_fbtn": null,
            "r": null, "wt": null
          }
        }
      ]
    }

GENBEAM sections (Sesam-style numeric properties with no geometric
profile) are skipped at write time — the viewer-side reader can't
tessellate them anyway. Beams without ``fem_sec.section`` or
``fem_sec.local_z`` are dropped for the same reason.
"""
from __future__ import annotations

import json
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.api.spatial import Assembly

SCHEMA_VERSION = 1


def _section_to_dict(section) -> dict | None:
    """Serialize a :class:`ada.Section` into a JSON-friendly dict.

    Returns ``None`` for sections that can't be reconstructed
    geometrically (e.g. GENBEAM with property numbers only).
    """
    sec_type = getattr(section, "type", None)
    if sec_type is None:
        type_name = None
    else:
        # ``BaseTypes`` is an Enum whose ``.value`` (e.g. "HP", "BOX")
        # is what ``BaseTypes.from_str`` expects on the read side.
        # Anything stringy from a Section already constructed with a
        # raw type str falls through unchanged.
        type_name = sec_type.value if hasattr(sec_type, "value") else str(sec_type)
    if type_name == "GENBEAM":
        return None
    return {
        "name": section.name,
        "type": type_name,
        "h": _maybe_float(section.h),
        "w_top": _maybe_float(getattr(section, "w_top", None)),
        "w_btn": _maybe_float(getattr(section, "w_btn", None)),
        "t_w": _maybe_float(getattr(section, "t_w", None)),
        "t_ftop": _maybe_float(getattr(section, "t_ftop", None)),
        "t_fbtn": _maybe_float(getattr(section, "t_fbtn", None)),
        "r": _maybe_float(getattr(section, "r", None)),
        "wt": _maybe_float(getattr(section, "wt", None)),
    }


def _maybe_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _iter_line_elems(assembly: "Assembly"):
    """Yield every FEM line element under ``assembly`` (assembly +
    parts), each paired with the FEM container that owns it so any
    later lookup (e.g. by node id) stays unambiguous."""
    yield from assembly.fem.elements.lines
    for part in assembly.get_all_parts_in_assembly(True):
        if part.fem is assembly.fem:
            continue
        yield from part.fem.elements.lines


def build_beams_payload(assembly: "Assembly") -> dict:
    """Walk ``assembly`` and return the JSON-ready beams sidecar dict.

    Beams whose section is None / GENBEAM, or whose ``local_z`` is
    missing, are silently dropped — the viewer-side reader couldn't
    tessellate them anyway, and counting them in the sidecar would
    just confuse coverage telemetry on the read side.
    """
    units_attr = getattr(assembly, "units", "m")
    units = units_attr.value if hasattr(units_attr, "value") else str(units_attr)
    beams: list[dict] = []
    for elem in _iter_line_elems(assembly):
        fem_sec = elem.fem_sec
        if fem_sec is None:
            continue
        if fem_sec.local_z is None:
            continue
        sec_dict = _section_to_dict(fem_sec.section) if fem_sec.section else None
        if sec_dict is None:
            continue
        n0 = elem.nodes[0]
        n1 = elem.nodes[-1]
        material = fem_sec.material
        beams.append(
            {
                "elem_id": int(elem.id),
                "n0_id": int(n0.id),
                "n1_id": int(n1.id),
                "n0": [float(n0.p[0]), float(n0.p[1]), float(n0.p[2])],
                "n1": [float(n1.p[0]), float(n1.p[1]), float(n1.p[2])],
                "local_z": [float(c) for c in fem_sec.local_z],
                "material_name": material.name if material is not None else None,
                "section": sec_dict,
            }
        )
    return {"version": SCHEMA_VERSION, "units": units, "beams": beams}


def dump_beams_sidecar(assembly: "Assembly", path: pathlib.Path) -> int:
    """Write the per-beam metadata sidecar next to the analysis deck.

    Returns the number of beams emitted, mostly for tests / callers
    that want to log coverage at write time.
    """
    payload = build_beams_payload(assembly)
    pathlib.Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True))
    return len(payload["beams"])


__all__ = ["SCHEMA_VERSION", "build_beams_payload", "dump_beams_sidecar"]

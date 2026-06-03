"""Per-element section + lineage sidecar emitted next to the Code Aster
.med / .rmed.

Two responsibilities, kept under the same file because they share the
same data source (the in-memory Assembly's FEM sections + their
``refs`` back-reference to the source Beam/Plate):

* **Beam-solid tessellation data** — per-line-element section + axis
  + endpoint coords. The MED format carries the mesh and result
  fields but no section profiles, no per-element material assignment,
  and no beam orientation (``VECT_Y`` / ``local_z``); those live in
  the .comm deck. The streaming-viewer bake needs them to tessellate
  beams as 3D extruded solids, mirroring what the SIF / Abaqus paths
  get for free from their native formats.

* **CAD↔FEA lineage** — top-level ``assembly_guid`` + per-element
  ``parent_object_guid`` so the viewer can resolve a clicked FEA
  element back to the source Beam or Plate without name matching.
  Beams and plates are written under two parallel arrays so the read
  side can aggregate them independently.

This module dumps a ``<name>.adapy_fem.json`` sidecar next to the
.med at write time. The Code Aster solver round-trips the .med to
.rmed so the sidecar ends up sitting next to the .rmed result that
the viewer's bake worker opens; the RMED stream reader picks it up
by basename.

Schema (version 3):

.. code-block:: json

    {
      "version": 3,
      "units": "m",
      "assembly_guid": "<adapy Assembly.guid>",   // v2+: lineage anchor
      "beams": [
        {
          "elem_id": 17,
          "n0_id": 4, "n1_id": 5,
          "n0": [0.0, 0.0, 0.0], "n1": [1.0, 0.0, 0.0],
          "local_z": [0.0, 0.0, 1.0],
          "material_name": "S355",
          "parent_object_guid": "<beam.guid>",     // v2+: CAD↔FEA link
          "parent_object_name": "BM_FLOOR_01",     // v2+: CAD↔FEA link
          "section": {
            "name": "HP220x10",
            "type": "BG",
            "h": 0.22, "w_top": 0.10, "w_btn": null,
            "t_w": 0.01,
            "t_ftop": 0.012, "t_fbtn": null,
            "r": null, "wt": null
          }
        }
      ],
      "plates": [                                 // v3: plate lineage
        {
          "elem_ids": [201, 202, 203, ...],
          "parent_object_guid": "<plate.guid>",
          "parent_object_name": "PL_DECK_07",
          "thickness": 0.012,
          "material_name": "S355"
        }
      ]
    }

Plates carry no per-element tessellation info (shells render
directly from the MED mesh); the array exists only to thread the
``parent_object_guid`` link from CAD plates to their meshed shell
elements through to the bake's lineage manifest.

v5 adds an optional top-level ``fem_concepts`` object — the FEA
*input* concepts (point masses, boundary conditions, per-case /
combination load scenarios) serialized in the same shape as the
``fem_concepts`` glTF-extension block. The .rmed result file holds
none of these (they're solver inputs), so the sidecar is the only
place they survive the .med→.rmed round-trip; the bake reads it
back into the result manifest so the viewer's FEM mode can overlay
masses / BCs / loads on a baked FEA-result GLB.

Schema is additive across versions: v1/v2 readers see new keys as
unknown and ignore them; v3 readers fall back gracefully when v1/v2
files don't carry the new arrays. So the bump is backward-
compatible in both directions.

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

SCHEMA_VERSION = 5


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


def _iter_shell_fem_sections(assembly: "Assembly"):
    """Yield every FemSection whose elements meshed from a CAD Plate.

    Shells (and only shells) hit the bake's plate-lineage path. We key
    off ``FemSection.thickness`` (non-None for shells, None for line
    sections) rather than walking elements one-by-one — every element
    in a section shares the same parent ``Plate``, so per-section
    aggregation matches the bake's lineage shape exactly."""
    fems = [assembly.fem] if assembly.fem is not None else []
    for part in assembly.get_all_parts_in_assembly(True):
        if part.fem is None or part.fem is assembly.fem:
            continue
        fems.append(part.fem)
    for fem in fems:
        for fem_sec in fem.sections:
            if getattr(fem_sec, "thickness", None) is None:
                continue
            yield fem_sec


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
        # CAD↔FEA lineage. ``FemSection.refs`` is populated during
        # meshing (src/ada/fem/sections.py:161 — ``refs=[beam]``); on a
        # FEM read back from a third-party file it stays empty, in
        # which case we just omit the lineage fields and the bake's
        # downstream lineage manifest skips this beam.
        parent_obj_guid: str | None = None
        parent_obj_name: str | None = None
        refs = getattr(fem_sec, "refs", None)
        if refs:
            parent_obj_guid = getattr(refs[0], "guid", None)
            parent_obj_name = getattr(refs[0], "name", None)
        beams.append(
            {
                "elem_id": int(elem.id),
                "n0_id": int(n0.id),
                "n1_id": int(n1.id),
                "n0": [float(n0.p[0]), float(n0.p[1]), float(n0.p[2])],
                "n1": [float(n1.p[0]), float(n1.p[1]), float(n1.p[2])],
                "local_z": [float(c) for c in fem_sec.local_z],
                "material_name": material.name if material is not None else None,
                "parent_object_guid": parent_obj_guid,
                "parent_object_name": parent_obj_name,
                "section": sec_dict,
            }
        )
    plates = _build_plates_lineage(assembly)
    payload: dict = {"version": SCHEMA_VERSION, "units": units, "beams": beams}
    if plates:
        payload["plates"] = plates
    # Top-level lineage anchor. ``Assembly.guid`` is set (for IFC
    # roundtrips) to ``IfcProject.GlobalId`` in store.py, so the same
    # value lands here as in the CAD GLB's ``ADA_EXT_data.assembly_guid``
    # — that's what the frontend matches files on.
    assembly_guid = getattr(assembly, "guid", None)
    if assembly_guid:
        payload["assembly_guid"] = assembly_guid
    # Dedup materials at the top level so the lineage reader can
    # surface full material properties (E, ρ, σ_y, ν) per group
    # without redundantly repeating them on every per-element entry
    # — most large FEA models use 1-3 materials across thousands of
    # sections. Names already appear on beam / plate entries as
    # ``material_name``; this dict is just the lookup.
    materials = _build_materials_dict(assembly)
    if materials:
        payload["materials"] = materials
    # FEA *input* concepts — point masses, boundary conditions, and
    # per-case/combination load scenarios. The .rmed result file holds
    # none of these (they're solver inputs), so the sidecar is the only
    # transport that survives the .med→.rmed round-trip; the bake reads
    # it back and injects it into the result manifest's ``fem_concepts``
    # so the viewer's FEM mode can overlay them on the result GLB. The
    # serialized shape matches the ``fem_concepts`` glTF-extension block
    # the CAD/FEM GLB producers emit, so the frontend renderer is shared.
    fem_concepts = _build_fem_concepts_dict(assembly)
    if fem_concepts:
        payload["fem_concepts"] = fem_concepts
    return payload


def _build_fem_concepts_dict(assembly: "Assembly") -> dict | None:
    """Serialize the assembly's masses + BCs + load scenarios to a JSON
    dict matching the ``fem_concepts`` extension block, or None when the
    model carries no such concepts."""
    from ada.extension.fem_concepts_builder import build_combined_fem_concepts

    concepts = build_combined_fem_concepts(assembly)
    if concepts is None:
        return None
    return concepts.model_dump(mode="json", exclude_none=True)


def _build_materials_dict(assembly: "Assembly") -> dict[str, dict]:
    """One dict entry per unique material referenced by any line or
    shell FemSection that survived to the sidecar. ``None`` materials
    and entries without numeric properties are skipped."""
    from ada.comms.msg_handling.object_metadata import material_to_dict

    seen: dict[str, dict] = {}
    fems = [assembly.fem] if assembly.fem is not None else []
    for part in assembly.get_all_parts_in_assembly(True):
        if part.fem is None or part.fem is assembly.fem:
            continue
        fems.append(part.fem)
    for fem in fems:
        for fem_sec in fem.sections:
            mat = getattr(fem_sec, "material", None)
            if mat is None:
                continue
            name = getattr(mat, "name", None)
            if not name or name in seen:
                continue
            md = material_to_dict(mat)
            if md is None:
                continue
            seen[name] = md
    return seen


def _build_plates_lineage(assembly: "Assembly") -> list[dict]:
    """One entry per FemSection that came from a CAD Plate.

    Skipped silently for shell sections without a CAD parent
    (``refs`` empty — e.g. shells read back from a third-party FEM
    that adapy never meshed). Element ids come straight from the
    section's elset; the bake's lineage aggregator wraps them in
    ``E{id}`` to match its element-range naming."""
    out: list[dict] = []
    for fem_sec in _iter_shell_fem_sections(assembly):
        refs = getattr(fem_sec, "refs", None)
        if not refs:
            continue
        parent = refs[0]
        parent_guid = getattr(parent, "guid", None)
        parent_name = getattr(parent, "name", None)
        if not parent_guid:
            continue
        elset = getattr(fem_sec, "elset", None)
        members = getattr(elset, "members", None) if elset is not None else None
        if not members:
            continue
        elem_ids = [int(e.id) for e in members if getattr(e, "id", None) is not None]
        if not elem_ids:
            continue
        material = getattr(fem_sec, "material", None)
        out.append(
            {
                "elem_ids": elem_ids,
                "parent_object_guid": parent_guid,
                "parent_object_name": parent_name,
                "thickness": _maybe_float(getattr(fem_sec, "thickness", None)),
                "material_name": material.name if material is not None else None,
            }
        )
    return out


def dump_beams_sidecar(assembly: "Assembly", path: pathlib.Path) -> int:
    """Write the per-beam metadata sidecar next to the analysis deck.

    Returns the number of beams emitted, mostly for tests / callers
    that want to log coverage at write time.
    """
    payload = build_beams_payload(assembly)
    pathlib.Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True))
    return len(payload["beams"])


__all__ = ["SCHEMA_VERSION", "build_beams_payload", "dump_beams_sidecar"]

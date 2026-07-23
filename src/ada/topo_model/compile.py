"""Compile a procedural cell-model document into GLB bytes.

The document is the viewer-side cellbuilder's commit format (see
``ada.comms.rest.procedural``): ``spaces``/``equipments``/``openings`` lists of
``ada.topology.entities`` pydantic dumps. Spaces become ``PrimBox``es feeding
the topology engine; equipment boxes render as box bodies under an Equipment
part. ``blueprint_name="none"`` skips the structural blueprint and renders the
raw space boxes — useful before/without a domain blueprint.
"""

from __future__ import annotations

import pathlib
import tempfile
from typing import Literal

import ada
from ada.topology import TopologyBuilder
from ada.topology.entities import TopoEquipment, TopoSpace

from .blueprint import SteelStru

__all__ = ["compile_procedural_doc"]


def _require_coords(entity, attrs: tuple[str, ...]) -> None:
    missing = [a for a in attrs if getattr(entity, a) is None]
    if missing:
        raise ValueError(f"entity {entity.NAME!r} is missing coordinates {missing}; cannot compile")


def _space_to_box(space: TopoSpace) -> ada.PrimBox:
    _require_coords(space, ("X", "Y", "Z", "DX", "DY", "DZ"))
    p1 = (space.X, space.Y, space.Z)
    p2 = (space.X + space.DX, space.Y + space.DY, space.Z + space.DZ)
    return ada.PrimBox(space.NAME, p1, p2, metadata={"PM_TOPO_OBJ": space.model_dump()})


def _equipment_to_object(eq: TopoEquipment) -> ada.Equipment | ada.PrimBox:
    """An equipment entity whose DESCRIPTION names a registered archetype
    (pump/tank/...) compiles into the full archetype — ports and IFC element
    class included; anything else renders as a plain box."""
    from .equipment import EQUIPMENT_ARCHETYPES

    _require_coords(eq, ("X", "Y", "Z", "LX", "LY", "LZ"))
    archetype = EQUIPMENT_ARCHETYPES.get((eq.DESCRIPTION or "").strip().lower())
    if archetype is not None:
        origin = (eq.X + eq.LX / 2, eq.Y + eq.LY / 2, eq.Z)
        return archetype(eq.NAME, origin, lx=eq.LX, ly=eq.LY, lz=eq.LZ)
    p1 = (eq.X, eq.Y, eq.Z)
    p2 = (eq.X + eq.LX, eq.Y + eq.LY, eq.Z + eq.LZ)
    return ada.PrimBox(eq.NAME, p1, p2, color="orange")


def compile_procedural_doc(
    doc: dict,
    *,
    blueprint_name: Literal["steel_stru", "none"] = "steel_stru",
    name: str = "ProceduralModel",
) -> bytes:
    """Parse ``doc``, build the model and return GLB bytes."""
    spaces = [TopoSpace(**s) for s in doc.get("spaces", [])]
    equipments = [TopoEquipment(**e) for e in doc.get("equipments", [])]
    if not spaces:
        raise ValueError("document has no spaces to compile")

    boxes = [_space_to_box(s) for s in spaces]

    if blueprint_name == "steel_stru":
        builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=SteelStru())
        builder.build()
        a = builder.get_output_assembly(name)
    else:
        a = ada.Assembly(name) / (ada.Part("Spaces") / boxes)

    if equipments:
        a.add_part(ada.Part("Equipment") / [_equipment_to_object(e) for e in equipments])

    with tempfile.TemporaryDirectory(prefix="procedural_glb_") as tmp:
        glb_path = pathlib.Path(tmp) / "model.glb"
        a.to_gltf(glb_path)
        return glb_path.read_bytes()

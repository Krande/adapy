"""Read/write a whole procedural cell-model as a multi-sheet Excel workbook.

The workbook has one sheet per entity type — ``Spaces`` (:class:`TopoSpace`),
``Equipments`` (:class:`TopoEquipment`), ``Openings`` (:class:`TopoOpening`),
``Systems`` (:class:`TopoSystem`; the nested ``CONNECTIONS`` list lands in a
single JSON cell) — plus a vertical ``Model`` sheet
(:class:`ProceduralModelMeta`) carrying the document-level scalars: the model
name, structural blueprint choice, whitelisted blueprint options, the design
ruleset slug and the ``equipment_cad`` / ``no_go_walls`` toggles.

This is the (de)serialization glue the generic :mod:`ada.serialize.xlsx`
serializer deliberately leaves to the caller: it maps a
:class:`~ada.topo_model.builder.ProceduralBuilder`'s owned objects onto the
sheet models and back.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import BaseModel, Field

from ada.serialize.xlsx import WorkbookSerializer
from ada.topology.entities import TopoEquipment, TopoOpening, TopoSpace, TopoStructure, TopoSystem

if TYPE_CHECKING:
    from .builder import ProceduralBuilder

__all__ = ["ProceduralModelMeta", "read_procedural_excel", "write_procedural_excel"]

# Whitelisted structural-blueprint option key -> the flat ``Model`` sheet column.
_OPTION_TO_META = {
    "reinforce_internal_walls": "REINFORCE_INTERNAL_WALLS",
    "reinforce_external_walls": "REINFORCE_EXTERNAL_WALLS",
    "enclosed_cells": "ENCLOSED_CELLS",
    "pl_thick": "PL_THICK",
    "wall_pl_thick": "WALL_PL_THICK",
    "stringer_spacing": "STRINGER_SPACING",
}


class ProceduralModelMeta(BaseModel):
    """The single-row ``Model`` sheet: everything about the model that is not a
    space/equipment/opening/system. Written vertically (key/value) so it reads as
    a small settings card."""

    SHEET_NAME: ClassVar[str] = "Model"
    ORIENTATION: ClassVar[str] = "VERTICAL"
    TAB_COLOR: ClassVar[str] = "FFC000"  # HEX string without '#'
    HIDE_IN_EXCEL: ClassVar[list[str]] = []

    NAME: Annotated[str, Field(description="Name of the model")] = "ProceduralModel"
    BLUEPRINT: Annotated[
        Literal["steel_stru", "none"], Field(description="Structural blueprint (or 'none' for raw boxes)")
    ] = "steel_stru"
    LOD: Annotated[Literal["sim", "detail"], Field(description="Level of detail to compile")] = "sim"
    DESIGN_RULES: Annotated[str | None, Field(description="Named design-ruleset slug (routing/penetration)")] = None
    EQUIPMENT_CAD: Annotated[bool, Field(description="Render catalog equipment as their linked CAD geometry")] = False
    NO_GO_WALLS: Annotated[bool, Field(description="Block interior walls from in-plane routing")] = False

    # Whitelisted structural blueprint options (flat; unset = blueprint default).
    REINFORCE_INTERNAL_WALLS: Annotated[bool | None, Field(description="Plate + stiffen shared internal walls")] = None
    REINFORCE_EXTERNAL_WALLS: Annotated[bool | None, Field(description="Plate + stiffen outer walls")] = None
    ENCLOSED_CELLS: Annotated[
        list[str] | None,
        Field(description="Cells to fully enclose (all faces plated)", json_schema_extra={"excel": {"codec": "jsonlist"}}),
    ] = None
    PL_THICK: Annotated[float | None, Field(description="Deck plate thickness (m)")] = None
    WALL_PL_THICK: Annotated[float | None, Field(description="Wall plate thickness (m)")] = None
    STRINGER_SPACING: Annotated[float | None, Field(description="Stringer spacing (m)")] = None

    def blueprint_options(self) -> dict:
        """The whitelisted structural options as a dict (only the set ones),
        ready to hand to :class:`~ada.topo_model.blueprint.SteelStru`."""
        out: dict = {}
        for key, meta_field in _OPTION_TO_META.items():
            value = getattr(self, meta_field)
            if value is not None:
                out[key] = value
        return out

    @classmethod
    def from_builder(cls, builder: "ProceduralBuilder") -> "ProceduralModelMeta":
        kwargs: dict = {
            "NAME": builder.name,
            "BLUEPRINT": builder.blueprint_name,
            "LOD": builder.lod,
            "DESIGN_RULES": builder.design_rules_slug,
            "EQUIPMENT_CAD": builder.equipment_cad,
            "NO_GO_WALLS": builder.no_go_walls,
        }
        for key, meta_field in _OPTION_TO_META.items():
            if key in builder.blueprint_options:
                kwargs[meta_field] = builder.blueprint_options[key]
        return cls(**kwargs)


# One sheet per entity type; the Model sheet is registered last. The multi variant
# also carries a ``Structures`` sheet (one topology model per row).
_ENTITY_MODELS = (TopoSpace, TopoEquipment, TopoOpening, TopoSystem)


def _serializer(multi: bool = False) -> WorkbookSerializer:
    s = WorkbookSerializer()
    models = (TopoStructure, *_ENTITY_MODELS) if multi else _ENTITY_MODELS
    for model in (*models, ProceduralModelMeta):
        s.register(model)
    return s


def write_procedural_excel(builder: "ProceduralBuilder", path: str | pathlib.Path) -> None:
    """Write ``builder``'s owned model to a workbook at ``path``. When the builder
    has structures, a ``Structures`` sheet is added (the entities already carry
    their ``STRUCTURE_NAME``)."""
    multi = bool(builder.structures)
    instances: list = [*builder.spaces, *builder.equipments, *builder.openings, *builder.systems]
    if multi:
        instances = [*builder.structures, *instances]
    instances.append(ProceduralModelMeta.from_builder(builder))
    _serializer(multi=multi).write(instances, str(path))


def read_procedural_excel(path: str | pathlib.Path, multi: bool = False) -> dict:
    """Read a procedural workbook into entity lists + a single
    :class:`ProceduralModelMeta`. With ``multi=True`` the ``Structures`` sheet is
    parsed too (``"structures"`` in the result)."""
    by_type = _serializer(multi=multi).read(str(path))
    meta_rows = by_type.get(ProceduralModelMeta) or [ProceduralModelMeta()]
    out = {
        "spaces": by_type.get(TopoSpace, []),
        "equipments": by_type.get(TopoEquipment, []),
        "openings": by_type.get(TopoOpening, []),
        "systems": by_type.get(TopoSystem, []),
        "meta": meta_rows[0],
    }
    if multi:
        out["structures"] = by_type.get(TopoStructure, [])
    return out

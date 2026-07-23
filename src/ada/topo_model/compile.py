"""Compile a procedural cell-model document into GLB bytes.

The document is the viewer-side cellbuilder's commit format (see
``ada.comms.rest.procedural``): ``spaces``/``equipments``/``openings``/``systems``
lists of ``ada.topology.entities`` pydantic dumps (plus a small system schema).
Spaces become ``PrimBox``es feeding the topology engine; equipment archetypes
render with ports; systems wire their equipment ports, route over the model
grid and render as pipe/cable runs (with penetration details where they cross a
built wall). ``blueprint_name="none"`` skips the structural blueprint and
renders the raw space boxes — useful before/without a domain blueprint.
"""

from __future__ import annotations

import pathlib
import tempfile
from typing import Literal

import ada
from ada.topology import CellGrid, TopologyBuilder
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


# Whitelisted doc["blueprint"] options forwarded to SteelStru — never **kwargs
# straight from user input.
_BLUEPRINT_OPTION_KEYS = ("reinforce_internal_walls", "pl_thick", "wall_pl_thick", "stringer_spacing")


def _blueprint_options(doc: dict) -> dict:
    opts = doc.get("blueprint") or {}
    if not isinstance(opts, dict):
        return {}
    return {k: opts[k] for k in _BLUEPRINT_OPTION_KEYS if k in opts}


def _make_system(spec: dict):
    from ada.api.systems import CableSystem, DuctSystem, ElectricalSystem, PipingSystem

    cls = {
        "piping": PipingSystem,
        "duct": DuctSystem,
        "cable": CableSystem,
        "electrical": ElectricalSystem,
    }.get((spec.get("TYPE") or "piping").lower(), PipingSystem)
    return cls(spec["NAME"], medium=spec.get("MEDIUM"))


def _routing_grid(spaces: list[TopoSpace], equipments: list, spacing: float = 0.5) -> CellGrid:
    """A uniform lattice spanning the union of the space boxes plus a headroom
    level above the top deck (so runs can climb over equipment)."""
    xs, ys, zs = [], [], []
    for s in spaces:
        xs += [s.X, s.X + s.DX]
        ys += [s.Y, s.Y + s.DY]
        zs += [s.Z, s.Z + s.DZ]
    for eq in equipments:
        if isinstance(eq, ada.Equipment):
            zs.append(float(eq.origin[2]) + eq.lz)
    headroom = spacing * 3
    return CellGrid.from_bounds(
        (min(xs), min(ys), min(zs)),
        (max(xs), max(ys), max(zs) + headroom),
        spacing=spacing,
    )


def _occupy_equipment(grid: CellGrid, eq: ada.Equipment) -> None:
    ox, oy, oz = (float(v) for v in eq.origin)
    x0, x1 = ox - eq.lx / 2, ox + eq.lx / 2
    y0, y1 = oy - eq.ly / 2, oy + eq.ly / 2
    z0, z1 = oz, oz + eq.lz
    tol = 1e-9
    for ix, x in enumerate(grid.x_list):
        if not (x0 + tol < x < x1 - tol):
            continue
        for iy, y in enumerate(grid.y_list):
            if not (y0 + tol < y < y1 - tol):
                continue
            for iz, z in enumerate(grid.z_list):
                if z0 + tol < z < z1 - tol:
                    grid.register((ix, iy, iz), eq.name)


def _build_systems(doc: dict, equipment_map: dict, spaces: list[TopoSpace], cell_graph) -> list[ada.Part]:
    """Wire each system's equipment ports, route it over the model grid and
    render the run. Returns the parts to add (a Systems part, and a
    Penetrations part when systems cross built walls). System specs that can't
    be wired/routed (missing equipment/port, no route) are skipped with a
    logged warning so one bad run doesn't sink the whole compile."""
    from ada.config import logger
    from ada.topology.routing import RoutingError

    specs = doc.get("systems") or []
    if not specs:
        return []

    grid = _routing_grid(spaces, list(equipment_map.values()))
    for eq in equipment_map.values():
        _occupy_equipment(grid, eq)

    systems_part = ada.Part("Systems")
    built_systems = []
    for spec in specs:
        try:
            system = _make_system(spec)
            for conn in spec.get("CONNECTIONS") or []:
                eq = equipment_map.get(conn["EQUIPMENT"])
                if eq is None:
                    raise RoutingError(f"unknown equipment {conn['EQUIPMENT']!r}")
                system.connect(eq, conn["PORT"])
            for geom in system.route(grid):
                systems_part.add_object(geom)
            built_systems.append(system)
        except (RoutingError, ValueError, KeyError) as exc:
            logger.warning("procedural: skipping system %r: %s", spec.get("NAME"), exc)

    parts: list[ada.Part] = []
    if list(systems_part.get_all_physical_objects()):
        parts.append(systems_part)

    if built_systems and cell_graph is not None:
        from .penetration import StandardPenetrations

        faces = cell_graph.get_internal_walls()
        if faces:
            pens = StandardPenetrations(systems=built_systems, faces=faces).build()
            if list(pens.get_all_physical_objects()):
                parts.append(pens)
    return parts


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

    cell_graph = None
    if blueprint_name == "steel_stru":
        builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=SteelStru(**_blueprint_options(doc)))
        builder.build()
        a = builder.get_output_assembly(name)
        cell_graph = builder.cell_graph
    else:
        a = ada.Assembly(name) / (ada.Part("Spaces") / boxes)

    equipment_map: dict[str, ada.Equipment] = {}
    if equipments:
        objects = [_equipment_to_object(e) for e in equipments]
        for obj in objects:
            if isinstance(obj, ada.Equipment):
                equipment_map[obj.name] = obj
        a.add_part(ada.Part("Equipment") / objects)

    for part in _build_systems(doc, equipment_map, spaces, cell_graph):
        a.add_part(part)

    with tempfile.TemporaryDirectory(prefix="procedural_glb_") as tmp:
        glb_path = pathlib.Path(tmp) / "model.glb"
        a.to_gltf(glb_path)
        return glb_path.read_bytes()

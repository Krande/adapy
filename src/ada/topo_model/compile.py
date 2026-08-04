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

import contextlib
import os
import pathlib
import tempfile
from typing import Literal

import ada
from ada.topology import CellGrid, TopologyBuilder
from ada.topology.entities import TopoEquipment, TopoSpace

from .blueprint import SteelStru

__all__ = ["compile_procedural_doc"]


@contextlib.contextmanager
def _stream_tessellation(pipeline: str = "libtess2"):
    """Render the procedural model through the NGEOM ``pipeline`` (libtess2 by
    default) rather than the OCC BatchTessellator, so analytic swept runs — the
    duct/cable-tray ``FixedReferenceSweptAreaSolid`` sweeps — tessellate upright
    along their curve (OCC mis-orients a non-circular swept profile). Respects an
    explicit ``ADA_STREAM_TESS_PIPELINE`` already in the environment (e.g. a
    worker/converter job engine) and restores the previous value on exit."""
    key = "ADA_STREAM_TESS_PIPELINE"
    prev = os.environ.get(key)
    if prev is None:
        os.environ[key] = pipeline
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(key, None)


def _require_coords(entity, attrs: tuple[str, ...]) -> None:
    missing = [a for a in attrs if getattr(entity, a) is None]
    if missing:
        raise ValueError(f"entity {entity.NAME!r} is missing coordinates {missing}; cannot compile")


def _space_to_box(space: TopoSpace) -> ada.PrimBox:
    _require_coords(space, ("X", "Y", "Z", "DX", "DY", "DZ"))
    p1 = (space.X, space.Y, space.Z)
    p2 = (space.X + space.DX, space.Y + space.DY, space.Z + space.DZ)
    return ada.PrimBox(space.NAME, p1, p2, metadata={"PM_TOPO_OBJ": space.model_dump()})


def _equipment_to_object(eq: TopoEquipment, resolver=None) -> ada.Equipment | ada.PrimBox:
    """Compile an equipment entity into a placed object. The entity's
    DESCRIPTION names its type: a per-scope catalog slug (resolved via
    ``resolver`` to a catalog doc — bbox/mass/ports/IFC class) takes precedence,
    then a built-in archetype (pump/tank/...); anything else renders as a plain
    box."""
    from .equipment import EQUIPMENT_ARCHETYPES, build_equipment_from_catalog

    _require_coords(eq, ("X", "Y", "Z", "LX", "LY", "LZ"))
    key = (eq.DESCRIPTION or "").strip()
    origin = (eq.X + eq.LX / 2, eq.Y + eq.LY / 2, eq.Z)

    catalog_doc = resolver(key) if resolver is not None and key else None
    if catalog_doc:
        return build_equipment_from_catalog(eq.NAME, origin, catalog_doc, lx=eq.LX, ly=eq.LY, lz=eq.LZ)

    archetype = EQUIPMENT_ARCHETYPES.get(key.lower())
    if archetype is not None:
        return archetype(eq.NAME, origin, lx=eq.LX, ly=eq.LY, lz=eq.LZ)

    p1 = (eq.X, eq.Y, eq.Z)
    p2 = (eq.X + eq.LX, eq.Y + eq.LY, eq.Z + eq.LZ)
    return ada.PrimBox(eq.NAME, p1, p2, color="orange")


# Whitelisted doc["blueprint"] options forwarded to SteelStru — never **kwargs
# straight from user input.
_BLUEPRINT_OPTION_KEYS = (
    "reinforce_internal_walls",
    "reinforce_external_walls",
    "enclosed_cells",
    "pl_thick",
    "wall_pl_thick",
    "stringer_spacing",
)


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
    level above the top deck (so runs can climb over equipment).

    The floor/roof decks sit on the space-Z boundaries; any lattice level that
    lands exactly on a deck plane is dropped so a horizontal run never lies *in*
    a floor plate — it routes just above (in the room) or below (a sub-floor void)
    instead. Vertical runs still cross a deck plane on the edge between the
    surrounding levels. A ``spacing`` sub-floor band below the lowest deck gives
    low outlets (e.g. a tank drain) somewhere to route beneath the plate."""
    xs, ys, zs = [], [], []
    deck_planes = set()
    for s in spaces:
        xs += [s.X, s.X + s.DX]
        ys += [s.Y, s.Y + s.DY]
        zs += [s.Z, s.Z + s.DZ]
        deck_planes.update((round(float(s.Z), 4), round(float(s.Z + s.DZ), 4)))
    for eq in equipments:
        if isinstance(eq, ada.Equipment):
            zs.append(float(eq.origin[2]) + eq.lz)
    headroom = spacing * 3
    grid = CellGrid.from_bounds(
        (min(xs), min(ys), min(zs) - spacing),  # a sub-floor band below the lowest deck
        (max(xs), max(ys), max(zs) + headroom),
        spacing=spacing,
    )
    # Drop interior levels coincident with a deck plane (keep the first/last so the
    # lattice never loses its bounds); margin < spacing so only exact hits go. With
    # the sub-floor band, the lowest deck is now interior and gets dropped too.
    margin = spacing * 0.25
    z = grid.z_list
    grid.z_list = [
        v for i, v in enumerate(z) if i == 0 or i == len(z) - 1 or all(abs(v - d) > margin for d in deck_planes)
    ]
    return grid


def _system_half_extent(system) -> float:
    """The routed run's cross-section half-extent (pipe radius / half the duct or
    tray width|height), i.e. how far the run's surface reaches from its
    centreline. Used as the equipment clearance so a run's *body*, not just its
    centreline, is kept clear of equipment."""
    r = getattr(system, "pipe_radius", None)
    if r is not None:
        return float(r)
    w = getattr(system, "duct_width", None) or getattr(system, "tray_width", None)
    h = getattr(system, "duct_height", None) or getattr(system, "tray_height", None)
    return 0.5 * max(float(w or 0.0), float(h or 0.0))


def _occupy_equipment(grid: CellGrid, eq: ada.Equipment, clearance: float = 0.0) -> None:
    """Mark grid nodes inside the equipment box — inflated by ``clearance`` — as
    occupied so A* routes around it. The clearance (a run's cross-section
    half-extent) keeps the run's body, not merely its centreline, from clipping
    the equipment; the inflated bounds are compared inclusively so nodes sitting
    exactly on the (inflated) face are blocked too."""
    ox, oy, oz = (float(v) for v in eq.origin)
    c = float(clearance)
    x0, x1 = ox - eq.lx / 2 - c, ox + eq.lx / 2 + c
    y0, y1 = oy - eq.ly / 2 - c, oy + eq.ly / 2 + c
    z0, z1 = oz - c, oz + eq.lz + c
    tol = 1e-9
    for ix, x in enumerate(grid.x_list):
        if not (x0 - tol <= x <= x1 + tol):
            continue
        for iy, y in enumerate(grid.y_list):
            if not (y0 - tol <= y <= y1 + tol):
                continue
            for iz, z in enumerate(grid.z_list):
                if z0 - tol <= z <= z1 + tol:
                    grid.register((ix, iy, iz), eq.name)


def _build_systems(
    doc: dict, equipment_map: dict, spaces: list[TopoSpace], cell_graph, design_rules=None
) -> list[ada.Part]:
    """Wire each system's equipment ports then drive both engine phases with the
    ``design_rules`` ruleset (plan the routes, plan the penetrations, model the
    runs and their details). Returns the parts to add (a Systems part, and a
    Penetrations part when systems cross built walls). Specs that can't be wired
    (missing equipment/port) are skipped here; runs that can't be routed are
    skipped inside the engine (``skip_failed=True``) — so one bad run doesn't
    sink the whole compile."""
    from ada.api.systems import PortDirection
    from ada.config import logger
    from ada.topology import run_design
    from ada.topology.routing import RoutingError

    from .penetration import standard_design_rules

    specs = doc.get("systems") or []
    if not specs:
        return []

    grid = _routing_grid(spaces, list(equipment_map.values()))

    # Phase 0: wire ports (spec -> connected System). Connection errors (unknown
    # equipment/port) drop the whole system before it reaches the engine.
    built_systems = []
    for spec in specs:
        try:
            system = _make_system(spec)
            for conn in spec.get("CONNECTIONS") or []:
                # A site terminal (model-boundary input/output) instead of an
                # equipment port — closes a system that would otherwise dangle.
                if conn.get("SITE"):
                    pos = tuple(float(v) for v in (conn.get("POSITION") or (0.0, 0.0, 0.0)))
                    direction = PortDirection[str(conn.get("DIRECTION") or "IN").upper()]
                    # Orientation of the terminal nozzle (the outward vector the
                    # run leaves the site boundary along). Defaults to +Z when the
                    # doc doesn't specify one, matching connect_site's own default.
                    dvec = tuple(float(v) for v in (conn.get("DIRECTION_VECTOR") or (0.0, 0.0, 1.0)))
                    system.connect_site(conn["SITE"], pos, direction, dvec)
                    continue
                eq = equipment_map.get(conn["EQUIPMENT"])
                if eq is None:
                    raise RoutingError(f"unknown equipment {conn['EQUIPMENT']!r}")
                system.connect(eq, conn["PORT"])
            built_systems.append(system)
        except (RoutingError, ValueError, KeyError) as exc:
            logger.warning("procedural: skipping system %r: %s", spec.get("NAME"), exc)

    if not built_systems:
        return []

    # Occupy each equipment box inflated by the widest run's cross-section
    # half-extent, so A* keeps every run's *body* (not just its centreline) clear
    # of equipment. One shared clearance (the max over all systems) keeps the grid
    # single-pass while guaranteeing no run clips a box.
    clearance = max((_system_half_extent(s) for s in built_systems), default=0.0)
    for eq in equipment_map.values():
        _occupy_equipment(grid, eq, clearance)

    rules = design_rules if design_rules is not None else standard_design_rules()
    # Only walls the blueprint actually BUILT (a plate part tagged on the face) can
    # be penetrated — a run crossing an unbuilt cell boundary (e.g. two open cells
    # with no wall between them) must not get a sleeve/hole detail.
    members = (
        [w for w in cell_graph.get_internal_walls() if getattr(w, "associated_part", None) is not None]
        if cell_graph is not None
        else []
    )
    # One fine lattice for all systems (precise detours); each planned run's body
    # is marked occupied so later systems route around it, and swept runs are then
    # pulled taut in the clear corridor for smooth, well-separated bends.
    result = run_design(
        built_systems,
        cell_graph=cell_graph,
        grid=grid,
        members=members,
        rules=rules,
        skip_failed=True,
        avoid_other_systems=True,
    )
    route_geometry = result.route_geometry
    penetration_parts = result.penetration_parts
    for name in result.skipped:
        logger.warning("procedural: skipping system %r: no route found", name)

    parts: list[ada.Part] = []
    systems_part = ada.Part("Systems")
    for geoms in route_geometry.values():
        for geom in geoms:
            # Adding a pipe realises its segments lazily (elbow generation). A
            # degenerate run that slips past the router still shouldn't sink the
            # whole compile — skip it with a warning, matching skip_failed above.
            try:
                systems_part.add_object(geom)
            except Exception as exc:  # noqa: BLE001 - last-resort per-run guard
                logger.warning("procedural: skipping route geometry %r: %s", getattr(geom, "name", geom), exc)
    if list(systems_part.get_all_physical_objects()):
        parts.append(systems_part)

    if penetration_parts:
        pens = ada.Part("Penetrations") / penetration_parts
        if list(pens.get_all_physical_objects()):
            parts.append(pens)
    return parts


def _cad_placement(eq: TopoEquipment, mesh) -> tuple:
    """Translation that maps the CAD mesh's min corner onto the placed cell's
    ``(X, Y, Z)`` corner, so the real geometry sits where the equipment box
    would have. Returns ``(dx, dy, dz)``."""
    bmin = mesh.bounds[0]
    return (eq.X - float(bmin[0]), eq.Y - float(bmin[1]), eq.Z - float(bmin[2]))


def compile_procedural_doc(
    doc: dict,
    *,
    blueprint_name: Literal["steel_stru", "none"] = "steel_stru",
    name: str = "ProceduralModel",
    equipment_resolver=None,
    cad_scene_resolver=None,
    design_rules=None,
) -> bytes:
    """Parse ``doc``, build the model and return GLB bytes.

    ``equipment_resolver`` maps an equipment DESCRIPTION (a catalog slug) to a
    catalog document (bbox/mass/ports/IFC class). The worker supplies one backed
    by the per-scope equipment-type catalog; when omitted, only the built-in
    archetypes are available.

    ``design_rules`` is a :class:`~ada.topology.design_rules.DesignRules` whose
    four callables fully encompass the routing/penetration rules for both engine
    phases. When omitted, the document's ``design_rules`` slug is resolved via
    the registry (``ada.topo_model.resolve_design_rules``); an unknown/absent
    slug falls back to the standard ruleset.

    ``cad_scene_resolver`` maps a catalog slug to a trimesh mesh loaded from the
    type's linked CAD asset. When ``doc["equipment_cad"]`` is set, catalog
    equipment with resolvable CAD geometry are built without their placeholder
    box body and the real CAD mesh is spliced into the output GLB at the cell
    footprint."""
    if design_rules is None:
        from .design_rulesets import resolve_design_rules

        design_rules = resolve_design_rules(doc.get("design_rules"))

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

    use_cad = bool(doc.get("equipment_cad")) and cad_scene_resolver is not None
    equipment_map: dict[str, ada.Equipment] = {}
    cad_placements: list[tuple] = []  # (mesh, (dx, dy, dz))
    if equipments:
        objects = []
        for e in equipments:
            slug = (e.DESCRIPTION or "").strip()
            cad_mesh = cad_scene_resolver(slug) if (use_cad and slug) else None
            if cad_mesh is not None:
                from .equipment import build_equipment_from_catalog

                _require_coords(e, ("X", "Y", "Z", "LX", "LY", "LZ"))
                origin = (e.X + e.LX / 2, e.Y + e.LY / 2, e.Z)
                catalog_doc = equipment_resolver(slug) if equipment_resolver is not None else None
                obj = build_equipment_from_catalog(
                    e.NAME, origin, catalog_doc or {}, lx=e.LX, ly=e.LY, lz=e.LZ, add_body=False
                )
                cad_placements.append((cad_mesh, _cad_placement(e, cad_mesh)))
                objects.append(obj)
            else:
                objects.append(_equipment_to_object(e, equipment_resolver))
        for obj in objects:
            if isinstance(obj, ada.Equipment):
                equipment_map[obj.name] = obj
        a.add_part(ada.Part("Equipment") / objects)

    for part in _build_systems(doc, equipment_map, spaces, cell_graph, design_rules):
        a.add_part(part)

    # Render through the NGEOM stream so the analytic swept duct/cable-tray runs
    # tessellate upright along their curve (see _stream_tessellation).
    with _stream_tessellation():
        # When CAD geometry is spliced in, merge it into the assembly's trimesh
        # scene at the footprint transform and export that; otherwise take the
        # normal analytic to_gltf path.
        if cad_placements:
            import numpy as np

            scene = a.to_trimesh_scene()
            for mesh, (dx, dy, dz) in cad_placements:
                transform = np.eye(4)
                transform[:3, 3] = [dx, dy, dz]
                scene.add_geometry(mesh, transform=transform)
            exported = scene.export(file_type="glb")
            return exported if isinstance(exported, bytes) else bytes(exported)

        with tempfile.TemporaryDirectory(prefix="procedural_glb_") as tmp:
            glb_path = pathlib.Path(tmp) / "model.glb"
            a.to_gltf(glb_path)
            return glb_path.read_bytes()

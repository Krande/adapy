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
from ada.topology.entities import TopoEquipment, TopoOpening, TopoSpace

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


def _opening_to_box(opening: TopoOpening) -> ada.PrimBox:
    """The negative-volume box of a placed opening (used as the subtracting tool
    when it overlaps a built plate). Carries the entity on ``PM_TOPO_OBJ`` for
    round-tripping, mirroring :func:`_space_to_box`."""
    p1, p2 = opening.get_p1(), opening.get_p2()
    lo = tuple(min(float(a), float(b)) for a, b in zip(p1, p2))
    hi = tuple(max(float(a), float(b)) for a, b in zip(p1, p2))
    return ada.PrimBox(opening.NAME, lo, hi, metadata={"PM_TOPO_OBJ": opening.model_dump()})


class _SpaceConfig:
    """Minimal opening ``parent_config``: resolves ``get_space`` against the
    doc's parsed spaces so a locally-placed opening (``USE_GLOBAL_COORDS=False``)
    can find its host space to compute its world box."""

    def __init__(self, spaces: list[TopoSpace]) -> None:
        self._spaces = list(spaces)

    def get_space(self, name: str, structure_name: str | None = None) -> TopoSpace | None:
        for s in self._spaces:
            if s.NAME == name and (structure_name is None or s.STRUCTURE_NAME == structure_name):
                return s
        return None


def _apply_openings(
    blueprint: SteelStru, assembly: ada.Assembly, spaces: list[TopoSpace], opening_docs: list[dict]
) -> None:
    """Cut each ``doc["openings"]`` entry into the built plates it overlaps and
    add its reinforcement framing (grouped under an ``Openings`` part). An opening
    that overlaps no built plate (e.g. ``blueprint_name`` built no walls) is a
    no-op; a failing cut is skipped with a warning so it never sinks the compile.
    A doc with ``openings: []`` behaves exactly as before."""
    from ada.config import logger

    if not opening_docs:
        return

    config = _SpaceConfig(spaces)
    reinforcement_parts: list[ada.Part] = []
    for o in opening_docs:
        try:
            opening = TopoOpening(**o)
            opening.parent_config = config
            part = blueprint.cut_opening(assembly, opening)
        except Exception as exc:  # noqa: BLE001 - one bad opening must not sink the compile
            logger.warning("procedural: skipping opening %r: %s", (o or {}).get("NAME"), exc)
            continue
        if part is not None and list(part.get_all_physical_objects()):
            reinforcement_parts.append(part)

    if reinforcement_parts:
        assembly.add_part(ada.Part("Openings") / reinforcement_parts)


def _equipment_to_object(eq: TopoEquipment, resolver=None) -> ada.Equipment | ada.PrimBox:
    """Compile an equipment entity into a placed object. The entity's
    DESCRIPTION names its type: a per-scope catalog slug (resolved via
    ``resolver`` to a catalog doc — bbox/mass/ports/IFC class) takes precedence,
    then a built-in archetype (pump/tank/...); anything else renders as a plain
    box."""
    from .equipment import (
        EQUIPMENT_ARCHETYPES,
        apply_equipment_rotation,
        build_equipment_from_catalog,
        rotation_matrix,
    )

    _require_coords(eq, ("X", "Y", "Z", "LX", "LY", "LZ"))
    key = (eq.DESCRIPTION or "").strip()
    origin = (eq.X + eq.LX / 2, eq.Y + eq.LY / 2, eq.Z)
    rot_deg = eq.rotation_deg()

    catalog_doc = resolver(key) if resolver is not None and key else None
    if catalog_doc:
        obj = build_equipment_from_catalog(eq.NAME, origin, catalog_doc, lx=eq.LX, ly=eq.LY, lz=eq.LZ)
        obj = apply_equipment_rotation(obj, *rot_deg)
        obj._topo_rotation_deg = rot_deg  # so occupancy/clash tests use the ROTATED footprint
        return obj

    archetype = EQUIPMENT_ARCHETYPES.get(key.lower())
    if archetype is not None:
        obj = archetype(eq.NAME, origin, lx=eq.LX, ly=eq.LY, lz=eq.LZ)
        obj = apply_equipment_rotation(obj, *rot_deg)
        obj._topo_rotation_deg = rot_deg  # so occupancy/clash tests use the ROTATED footprint
        return obj

    p1 = (eq.X, eq.Y, eq.Z)
    p2 = (eq.X + eq.LX, eq.Y + eq.LY, eq.Z + eq.LZ)
    # A bare, un-typed box still honours its placement rotation (pivot = the
    # footprint centre = origin) so an anonymous equipment box spins too.
    rot = rotation_matrix(*rot_deg)
    if rot is not None:
        import numpy as np

        from .equipment import _oriented_box

        return _oriented_box(eq.NAME, p1, p2, "orange", rot, np.asarray(origin, dtype=float))
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


def _penetration_members(cell_graph) -> list:
    """Built walls AND decks a routed run can penetrate (get a cutout + detail
    for). Only faces the blueprint actually BUILT (a plate part tagged on the face)
    count — a run crossing an unbuilt cell boundary (two open cells, no wall/deck
    between them) must NOT get a sleeve/hole. INTERNAL and EXTERNAL built walls
    qualify (a riser that exits/re-enters the envelope through the built outer skin,
    e.g. the demo's DeckTie riser through the x=0 external wall), and so do the
    built floor/roof DECKS — a vertical riser climbing between stacked cells crosses
    the deck plate and needs its cutout just like a wall crossing. Deduped by
    identity across the four lists."""
    if cell_graph is None:
        return []
    seen: set[int] = set()
    members: list = []
    for f in (
        cell_graph.get_internal_walls()
        + cell_graph.get_external_walls()
        + cell_graph.get_internal_floors()
        + cell_graph.get_external_floors()
    ):
        if getattr(f, "associated_part", None) is None or id(f) in seen:
            continue
        seen.add(id(f))
        members.append(f)
    return members


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

    A run must never lie *in* a wall or floor plate (only cross one perpendicular,
    where a penetration detail is modelled). Every interior lattice line that
    lands exactly on a plate plane is therefore dropped — for decks (Z boundaries),
    interior walls (shared X/Y boundaries between cells) alike: a perpendicular
    crossing still spans the plane on the edge between the surrounding lines, but
    no node sits inside the plate so nothing routes along it. The first/last line
    of each axis (the outer envelope walls) is kept for bounds — built external
    walls are instead blocked as no-go obstacles in ``_build_systems``. A
    ``spacing`` sub-floor band below the lowest deck gives low outlets (e.g. a tank
    drain) somewhere to route beneath the plate."""
    xs, ys, zs = [], [], []
    x_planes, y_planes, deck_planes = set(), set(), set()
    for s in spaces:
        xs += [s.X, s.X + s.DX]
        ys += [s.Y, s.Y + s.DY]
        zs += [s.Z, s.Z + s.DZ]
        x_planes.update((round(float(s.X), 4), round(float(s.X + s.DX), 4)))
        y_planes.update((round(float(s.Y), 4), round(float(s.Y + s.DY), 4)))
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

    # Drop interior levels coincident with a plate plane (keep the first/last so
    # the lattice never loses its bounds); margin < spacing so only exact hits go.
    # With the sub-floor band, the lowest deck is now interior and gets dropped too.
    margin = spacing * 0.25

    def _drop(vals: list[float], planes: set[float]) -> list[float]:
        return [
            v for i, v in enumerate(vals) if i == 0 or i == len(vals) - 1 or all(abs(v - p) > margin for p in planes)
        ]

    grid.x_list = _drop(grid.x_list, x_planes)
    grid.y_list = _drop(grid.y_list, y_planes)
    grid.z_list = _drop(grid.z_list, deck_planes)
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


def _equipment_box(eq: ada.Equipment, clearance: float = 0.0):
    """The equipment's (clearance-inflated) body as an oriented box: a rotation
    matrix ``R`` (None when axis-aligned), the pivot (footprint centre = origin),
    and local min/max half-extents. A node/point ``q`` is inside the body when
    ``lo <= Rᵀ·(q - pivot) <= hi``. The pivot and local extents match how
    ``_oriented_box`` builds the rotated body, so occupancy and the real geometry
    agree even when the equipment is spun."""
    import numpy as np

    from .equipment import rotation_matrix

    c = float(clearance)
    pivot = np.array([float(v) for v in eq.origin])  # (X+LX/2, Y+LY/2, Z) — footprint centre
    lo = np.array([-eq.lx / 2 - c, -eq.ly / 2 - c, -c])
    hi = np.array([eq.lx / 2 + c, eq.ly / 2 + c, eq.lz + c])
    R = rotation_matrix(*getattr(eq, "_topo_rotation_deg", (0.0, 0.0, 0.0)))
    return R, pivot, lo, hi


def _point_in_equipment(q, R, pivot, lo, hi, tol: float = 1e-9) -> bool:
    """Whether world point ``q`` lies inside the oriented equipment box described
    by :func:`_equipment_box`."""
    import numpy as np

    local = (R.T @ (np.asarray(q, float) - pivot)) if R is not None else (np.asarray(q, float) - pivot)
    return bool(np.all(local >= lo - tol) and np.all(local <= hi + tol))


def _occupy_equipment(grid: CellGrid, eq: ada.Equipment, clearance: float = 0.0) -> None:
    """Mark grid nodes inside the equipment body — inflated by ``clearance`` — as
    occupied so A* routes around it. The clearance (a run's cross-section
    half-extent) keeps the run's body, not merely its centreline, from clipping
    the equipment. Honours the equipment's placement rotation: a spun box occupies
    its ROTATED footprint (a switchboard turned 90° pokes out where its unrotated
    AABB never reached), so a run no longer grazes the real body."""
    import numpy as np

    R, pivot, lo, hi = _equipment_box(eq, clearance)
    tol = 1e-9
    if R is None:
        x0, y0, z0 = pivot + lo
        x1, y1, z1 = pivot + hi
    else:
        # Prune the grid scan to the rotated body's world AABB (8 rotated corners).
        corners = np.array(
            [
                [lo[0], lo[1], lo[2]],
                [hi[0], lo[1], lo[2]],
                [lo[0], hi[1], lo[2]],
                [hi[0], hi[1], lo[2]],
                [lo[0], lo[1], hi[2]],
                [hi[0], lo[1], hi[2]],
                [lo[0], hi[1], hi[2]],
                [hi[0], hi[1], hi[2]],
            ]
        )
        world = (R @ corners.T).T + pivot
        x0, y0, z0 = world.min(axis=0)
        x1, y1, z1 = world.max(axis=0)
    for ix, x in enumerate(grid.x_list):
        if not (x0 - tol <= x <= x1 + tol):
            continue
        for iy, y in enumerate(grid.y_list):
            if not (y0 - tol <= y <= y1 + tol):
                continue
            for iz, z in enumerate(grid.z_list):
                if not (z0 - tol <= z <= z1 + tol):
                    continue
                if R is None or _point_in_equipment((x, y, z), R, pivot, lo, hi):
                    grid.register((ix, iy, iz), eq.name)


def _augment_grid_with_ports(grid: CellGrid, built_systems: list) -> None:
    """Insert every system's port (and nozzle-stub) coordinates as grid lines
    BEFORE equipment occupancy is stamped.

    A run leaves each port along the port's world position and a one-cell stub;
    the router inserts those exact coordinates as grid lines so it can land on the
    port cleanly. If that happens per-system DURING routing (as the swept runs do)
    the new line is added AFTER ``_occupy_equipment`` already ran — so its nodes
    that fall inside an equipment box carry no occupancy, and a later run threads
    straight through that box along the fresh, un-blocked line (the drain routed
    through the pump). Front-loading every port/stub line here means occupancy is
    stamped onto ALL the lines the router will use, closing that corridor; the
    per-system augmentation then finds the line already present and is a no-op."""
    from ada.topology.routing import _grid_spacing, _port_stub, augment_grid_with_points

    stub_len = _grid_spacing(grid)
    pts = []
    for system in built_systems:
        for port in getattr(system, "ports", []):
            pts.append(port.get_global_position())
            stub = _port_stub(port, stub_len)
            if stub is not None:
                pts.append(stub)
    if pts:
        augment_grid_with_points(grid, pts)


def _flag_equipment_clashes(systems: list, equipment_map: dict) -> None:
    """Append a :class:`RunWarning` to any system whose routed body still enters a
    NON-endpoint equipment box. Grid occupancy already keeps every centreline out
    of every box; this catches the rare residual (a forced nozzle stub past a
    neighbour placed at the port) so it surfaces as a warning the user or the
    relocation optimiser can clear, instead of a silent clash."""
    import numpy as np

    from ada.topology.routing import RunWarning, run_half_extent

    for system in systems:
        poly = getattr(system, "routed_path", None)
        if not poly or len(poly) < 2:
            continue
        own = {p.parent.name for p in system.ports if getattr(p, "parent", None) is not None}
        half = run_half_extent(system)
        # Inflate each box by the run's half-extent so the run's BODY (not just its
        # centreline) is what's tested against; honours equipment rotation.
        boxes = [(name, *_equipment_box(eq, half)) for name, eq in equipment_map.items()]
        pts = [np.array([float(p[0]), float(p[1]), float(p[2])]) for p in poly]
        clashes: dict[str, np.ndarray] = {}
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            for t in np.linspace(0.0, 1.0, 24):
                q = a + (b - a) * t
                for name, R, pivot, lo, hi in boxes:
                    if name in own or name in clashes:
                        continue
                    if _point_in_equipment(q, R, pivot, lo, hi):
                        clashes[name] = q
        warnings = getattr(system, "route_warnings", None)
        if warnings is None:
            warnings = []
            system.route_warnings = warnings
        for name, q in sorted(clashes.items()):
            warnings.append(
                RunWarning(
                    position=(float(q[0]), float(q[1]), float(q[2])),
                    message=f"run body passes through equipment {name!r}",
                    suggestion=f"move {name} clear of the run, or re-route (try Propose relocations)",
                )
            )


def _wire_systems(specs: list[dict], equipment_map: dict) -> list:
    """Wire each system spec's equipment ports (and site terminals) into a
    connected :class:`~ada.api.systems.base.System`. Connection errors (unknown
    equipment/port, category mismatch) drop that whole system with a warning
    before it reaches the engine, so one bad spec doesn't sink the rest.

    Factored out of :func:`_build_systems` so the relocation engine
    (:mod:`ada.topo_model.relocate`) wires systems the exact same way when it
    re-routes candidate layouts."""
    from ada.api.systems import PortDirection
    from ada.config import logger
    from ada.topology.routing import RoutingError

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
    return built_systems


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
    from ada.config import logger
    from ada.topology import run_design

    from .penetration import standard_design_rules

    specs = doc.get("systems") or []
    if not specs:
        return []

    grid = _routing_grid(spaces, list(equipment_map.values()))

    # Phase 0: wire ports (spec -> connected System).
    built_systems = _wire_systems(specs, equipment_map)

    if not built_systems:
        return []

    # Insert every port/stub coordinate as a grid line up front, so the occupancy
    # stamped below covers ALL the lines the router will use (a line inserted later,
    # mid-routing, would thread un-blocked straight through an equipment box).
    _augment_grid_with_ports(grid, built_systems)

    # Occupy each equipment box inflated by the widest run's cross-section
    # half-extent PLUS a small margin, so A* keeps every run's *body* (not just its
    # centreline) clear of a box with a gap rather than grazing it. One shared
    # clearance (the max over all systems) keeps the grid single-pass.
    half_extent = max((_system_half_extent(s) for s in built_systems), default=0.0)
    # Margin is at LEAST 5 cm beyond the body (max, not min) so even a thin pipe
    # keeps a real gap from a box instead of grazing it — a 5 cm-margin floor was
    # the difference between the drain skimming the switchboard and clearing it.
    clearance = half_extent + max(0.05, 0.25 * half_extent) if half_extent else 0.0
    for eq in equipment_map.values():
        _occupy_equipment(grid, eq, clearance)

    rules = design_rules if design_rules is not None else standard_design_rules()
    members = _penetration_members(cell_graph)
    # Built EXTERNAL walls are blocked as no-go so nothing routes along the outer
    # skin (a lateral run buried in an envelope plate). They're never crossed
    # laterally — a site terminal sitting on one stays reachable via the A* goal
    # exemption, then leaves perpendicular. INTERIOR walls stay penetrable (a run
    # must cross them and get a sleeve); their in-plane travel is already prevented
    # by dropping their lattice line in _routing_grid. doc["no_go_walls"]
    # additionally blocks interior walls. DECKS are never no-go — a riser between
    # stacked cells must cross the deck (and get its cutout).
    ext_ids = {id(f) for f in cell_graph.get_external_walls()} if cell_graph is not None else set()
    no_go_faces = [f for f in members if not f.is_horizontal() and id(f) in ext_ids]
    if doc.get("no_go_walls"):
        no_go_faces += [f for f in members if not f.is_horizontal() and id(f) not in ext_ids]
    no_go_faces = no_go_faces or None

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
        no_go_faces=no_go_faces,
    )
    route_geometry = result.route_geometry
    penetration_parts = result.penetration_parts
    for name in result.skipped:
        logger.warning("procedural: skipping system %r: no route found", name)
    # Occupancy keeps every centreline out of every box, but a forced nozzle stub
    # can still graze a neighbour placed right at a port. Flag any residual so it's
    # visible (and actionable via the relocation optimiser), never a hidden clash.
    _flag_equipment_clashes(built_systems, equipment_map)
    # Surface bend-artifact warnings (corners a run left sharp because the route
    # was too cramped to round) so the cellbuilder can respace the offending run.
    for system in built_systems:
        for w in getattr(system, "route_warnings", []):
            logger.warning("procedural: route %s: %s", system.name, w)

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


def _cad_transform(eq: TopoEquipment, mesh):
    """4x4 that seats the CAD mesh where the equipment box would sit and applies
    the equipment's rotation about its footprint centre. First translate the
    mesh's min corner onto the placed cell's ``(X, Y, Z)`` corner, then spin the
    seated mesh about the pivot so the real geometry matches the ports."""
    import numpy as np

    from .equipment import rotation_matrix

    bmin = mesh.bounds[0]
    seat = np.eye(4)
    seat[:3, 3] = [eq.X - float(bmin[0]), eq.Y - float(bmin[1]), eq.Z - float(bmin[2])]
    rot = rotation_matrix(*eq.rotation_deg())
    if rot is None:
        return seat
    pivot = np.array([eq.X + eq.LX / 2.0, eq.Y + eq.LY / 2.0, eq.Z])
    spin = np.eye(4)
    spin[:3, :3] = rot
    to_pivot = np.eye(4)
    to_pivot[:3, 3] = pivot
    from_pivot = np.eye(4)
    from_pivot[:3, 3] = -pivot
    return to_pivot @ spin @ from_pivot @ seat


def compile_procedural_doc(
    doc: dict,
    *,
    blueprint_name: Literal["steel_stru", "none"] = "steel_stru",
    name: str = "ProceduralModel",
    equipment_resolver=None,
    cad_scene_resolver=None,
    design_rules=None,
    lod: Literal["sim", "detail"] = "sim",
) -> bytes:
    """Parse ``doc``, build the model and return GLB bytes.

    ``lod`` selects the level of detail: ``"sim"`` (default) is the analysis-grade
    simulation model; ``"detail"`` builds the richer detail model (deck plate edges
    trimmed to the girder flanges, I-girder joints modelled). Detail geometry is
    produced by the structural blueprint's ``detail`` mode; on ``blueprint_name=
    "none"`` the flag has no effect.

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
        blueprint = SteelStru(**_blueprint_options(doc), detail=(lod == "detail"))
        builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=blueprint)
        builder.build()
        a = builder.get_output_assembly(name)
        cell_graph = builder.cell_graph
        # Negative-volume openings cut the built wall/floor plates and add their
        # door/window reinforcement framing (no-op when the doc has no openings).
        _apply_openings(blueprint, a, spaces, doc.get("openings", []))
    else:
        a = ada.Assembly(name) / (ada.Part("Spaces") / boxes)

    use_cad = bool(doc.get("equipment_cad")) and cad_scene_resolver is not None
    equipment_map: dict[str, ada.Equipment] = {}
    cad_placements: list[tuple] = []  # (mesh, transform 4x4)
    if equipments:
        from .equipment import apply_equipment_rotation

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
                # The box body is omitted (CAD splices in), but the ports still
                # rotate so routing meets the spun CAD geometry at the right face.
                apply_equipment_rotation(obj, *e.rotation_deg())
                obj._topo_rotation_deg = e.rotation_deg()  # rotated footprint for occupancy/clash
                cad_placements.append((cad_mesh, _cad_transform(e, cad_mesh)))
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
            scene = a.to_trimesh_scene()
            for mesh, transform in cad_placements:
                scene.add_geometry(mesh, transform=transform)
            exported = scene.export(file_type="glb")
            return exported if isinstance(exported, bytes) else bytes(exported)

        with tempfile.TemporaryDirectory(prefix="procedural_glb_") as tmp:
            glb_path = pathlib.Path(tmp) / "model.glb"
            a.to_gltf(glb_path)
            return glb_path.read_bytes()

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
from typing import Literal

import ada
from ada.topology.entities import TopoEquipment, TopoOpening, TopoSpace

from ._grid import OrientedBox
from ._grid import augment_grid_with_ports as _augment_grid_with_ports
from ._grid import occupy_equipment as _occupy_equipment
from ._grid import routing_grid as _routing_grid
from .blueprint import SteelStru

# The grid/occupancy plumbing now lives in ``._grid``; re-exported under their
# historical private names because tests and ``ada.topo_model.relocate`` import
# them from this module.
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


def _apply_girder_joints(assembly: ada.Assembly) -> None:
    """Detect I-girder to I-girder intersections and model a joint at each,
    emitting visible connective geometry (gusset plate + weld beads) under a
    single ``ada.Part("Joints")``.

    Detection is the OCC-free numpy clash path
    (``assembly.connections.find(joint_func=detail_joint_map)``); the emitted
    ``Plate``/``Weld`` geometry tessellates in the libtess2/NGEOM stream. Wrapped
    so a joint failure only warns and never sinks the compile — mirroring
    :func:`_apply_openings`. Detail-mode only; sim mode never calls this."""
    from ada.config import logger

    try:
        from .detail_joints import collect_girder_joints

        joint_parts = [j.connection for j in collect_girder_joints(assembly)]
        if joint_parts:
            assembly.add_part(ada.Part("Joints") / joint_parts)
    except Exception as exc:  # noqa: BLE001 - a joint failure must never sink the compile
        logger.warning("procedural: girder-joint pass skipped: %s", exc)


def equipment_space_offset(eq: TopoEquipment, space) -> tuple[float, float, float]:
    """Global offset that seats an equipment relative to its containing cell.

    An equipment's ``X/Y/Z`` are LOCAL to its ``SPACE_NAME`` cell — the default
    when it is associated with a cell — unless ``GLOBAL_COORDS`` is set, matching
    :meth:`ada.topology.entities.TopoEquipment.get_origin` (and the sibling
    procedural engine, whose placement the simulation view reflects). So the
    offset is the cell's origin, plus the cell height for a ROOF-seated unit.
    Global-coord equipment — or one whose cell can't be resolved (fall back to
    treating ``X/Y/Z`` as global) — get no offset."""
    if eq.GLOBAL_COORDS or space is None:
        return (0.0, 0.0, 0.0)
    oz = float(space.Z or 0.0)
    if eq.SPACE_LOC == "ROOF":
        oz += float(space.DZ or 0.0)
    return (float(space.X or 0.0), float(space.Y or 0.0), oz)


def _equipment_to_object(
    eq: TopoEquipment, resolver=None, space_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> ada.Equipment | ada.PrimBox:
    """Compile an equipment entity into a placed object. The entity's
    DESCRIPTION names its type: a per-scope catalog slug (resolved via
    ``resolver`` to a catalog doc — bbox/mass/ports/IFC class) takes precedence,
    then a built-in archetype (pump/tank/...); anything else renders as a plain
    box. ``space_offset`` seats a cell-associated equipment at its cell (see
    :func:`equipment_space_offset`); it is ``(0,0,0)`` for global-coord units."""
    from .equipment import (
        EQUIPMENT_ARCHETYPES,
        apply_equipment_rotation,
        build_equipment_from_catalog,
        rotation_matrix,
    )

    _require_coords(eq, ("X", "Y", "Z", "LX", "LY", "LZ"))
    ox, oy, oz = space_offset
    bx, by, bz = eq.X + ox, eq.Y + oy, eq.Z + oz
    key = (eq.DESCRIPTION or "").strip()
    origin = (bx + eq.LX / 2, by + eq.LY / 2, bz)
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

    p1 = (bx, by, bz)
    p2 = (bx + eq.LX, by + eq.LY, bz + eq.LZ)
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
    # Section profiles (advertised as enum fields on the steel_stru blueprint —
    # see blueprint_catalog.STEEL_STRU_FIELDS). Forwarded RAW to SteelStru; each
    # value is a section string parsed by ada.sections.interpret_section_str. This
    # is the "box beams instead of I-beams" knob (a BG…/TUB… value).
    "girder_sec",
    "column_sec",
    "stringer_sec",
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
        boxes = [(name, OrientedBox.around_equipment(eq, half)) for name, eq in equipment_map.items()]
        pts = [np.array([float(p[0]), float(p[1]), float(p[2])]) for p in poly]
        clashes: dict[str, np.ndarray] = {}
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            for t in np.linspace(0.0, 1.0, 24):
                q = a + (b - a) * t
                for name, box in boxes:
                    if name in own or name in clashes:
                        continue
                    if box.contains(q):
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


def _cad_transform(eq: TopoEquipment, mesh, space_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)):
    """4x4 that seats the CAD mesh where the equipment box would sit and applies
    the equipment's rotation about its footprint centre. First translate the
    mesh's min corner onto the placed cell's ``(X, Y, Z)`` corner (shifted by
    ``space_offset`` for a cell-associated unit), then spin the seated mesh about
    the pivot so the real geometry matches the ports."""
    import numpy as np

    from .equipment import rotation_matrix

    ox, oy, oz = space_offset
    bx, by, bz = eq.X + ox, eq.Y + oy, eq.Z + oz
    bmin = mesh.bounds[0]
    seat = np.eye(4)
    seat[:3, 3] = [bx - float(bmin[0]), by - float(bmin[1]), bz - float(bmin[2])]
    rot = rotation_matrix(*eq.rotation_deg())
    if rot is None:
        return seat
    pivot = np.array([bx + eq.LX / 2.0, by + eq.LY / 2.0, bz])
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
    detailing: str | None = None,
    detailing_options: dict | None = None,
) -> bytes:
    """Parse ``doc``, build the model and return GLB bytes.

    A thin functional wrapper over :class:`ada.topo_model.builder.ProceduralBuilder`
    — the root object that owns the whole model (document, topology cell graph,
    blueprint, equipment, systems, design ruleset). Use the builder directly when
    you want to drive the phases individually or inspect the model between them;
    this wrapper is the batch one-shot.

    ``lod`` selects the level of detail: ``"sim"`` (default) is the analysis-grade
    simulation model; ``"detail"`` builds the richer detail model (deck plate edges
    trimmed to the girder flanges, I-girder joints modelled). Detail geometry is
    produced by the structural blueprint's ``detail`` mode; on ``blueprint_name=
    "none"`` the flag has no effect.

    ``detailing`` selects the fabrication-detail engine that adds connection
    joints after the structural build (``None``/``"none"`` = no detailing, the
    default — byte-identical to today; ``"adapy-default"`` runs the in-process
    built-in detailing engine). ``detailing_options`` carries the per-joint-type
    toggles/params. It is orthogonal to ``lod`` (which stays a tessellation
    knob), except ``lod=="detail"`` keeps firing adapy-default detailing for
    backward-compat.

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
    footprint.

    ``blueprint_name`` selects the structural blueprint. The document's own
    ``blueprint_name`` (a top-level scalar, kept OUT of the ``blueprint`` options
    forwarded to the blueprint) wins when present and recognised; the keyword
    argument is the fallback for a legacy doc that carries none (defaults to
    ``steel_stru``), so old documents compile unchanged."""
    from .builder import ProceduralBuilder

    doc_blueprint = doc.get("blueprint_name")
    if doc_blueprint in ("steel_stru", "none"):
        blueprint_name = doc_blueprint

    return ProceduralBuilder.from_dict(
        doc,
        name=name,
        blueprint_name=blueprint_name,
        lod=lod,
        detailing=detailing,
        detailing_options=detailing_options,
        equipment_resolver=equipment_resolver,
        cad_scene_resolver=cad_scene_resolver,
        design_rules=design_rules,
    ).compile()


def compile_procedural_doc_with_takeoff(
    doc: dict,
    *,
    blueprint_name: Literal["steel_stru", "none"] = "steel_stru",
    name: str = "ProceduralModel",
    equipment_resolver=None,
    cad_scene_resolver=None,
    design_rules=None,
    lod: Literal["sim", "detail"] = "sim",
    detailing: str | None = None,
    detailing_options: dict | None = None,
) -> tuple[bytes, dict]:
    """Compile ``doc`` and, in one pass, compute its quantity take-off.

    Same contract as :func:`compile_procedural_doc` (the batch one-shot) but keeps
    the built :class:`~ada.topo_model.builder.ProceduralBuilder` around so the
    structured model — beams, plates, routed pipe/duct/tray runs — can be reduced
    to a discipline-organised statistics document via
    :func:`ada.topo_model.takeoff.model_takeoff`. Returns ``(glb_bytes, stats)``.
    The worker stores ``stats`` as a ``.stats.json`` sibling of the GLB so the
    viewer's Stats panel can fetch it (see
    :func:`ada.comms.rest.procedural.procedural_stats_key`)."""
    glb_bytes, stats, _ = compile_procedural_doc_with_assembly(
        doc,
        blueprint_name=blueprint_name,
        name=name,
        equipment_resolver=equipment_resolver,
        cad_scene_resolver=cad_scene_resolver,
        design_rules=design_rules,
        lod=lod,
        detailing=detailing,
        detailing_options=detailing_options,
    )
    return glb_bytes, stats


def compile_procedural_doc_with_assembly(
    doc: dict,
    *,
    blueprint_name: Literal["steel_stru", "none"] = "steel_stru",
    name: str = "ProceduralModel",
    equipment_resolver=None,
    cad_scene_resolver=None,
    design_rules=None,
    lod: Literal["sim", "detail"] = "sim",
    detailing: str | None = None,
    detailing_options: dict | None = None,
) -> tuple[bytes, dict, "ada.Assembly"]:
    """Compile ``doc`` and return ``(glb_bytes, stats, assembly)`` — the same
    contract as :func:`compile_procedural_doc_with_takeoff` but ALSO handing back
    the compiled :class:`~ada.Assembly`. The worker needs the live model (not just
    the GLB triangles) to serialize the neutral structural artifact an EXTERNAL
    (Tier-B) detailing engine consumes — IFC bytes + a per-Beam section sidecar.
    Structural-only when ``detailing`` is ``None``/``"none"`` (an external detailing
    engine details the model out-of-process, so this pass adds no in-process joints)."""
    from .builder import ProceduralBuilder
    from .takeoff import model_takeoff

    doc_blueprint = doc.get("blueprint_name")
    if doc_blueprint in ("steel_stru", "none"):
        blueprint_name = doc_blueprint

    builder = ProceduralBuilder.from_dict(
        doc,
        name=name,
        blueprint_name=blueprint_name,
        lod=lod,
        detailing=detailing,
        detailing_options=detailing_options,
        equipment_resolver=equipment_resolver,
        cad_scene_resolver=cad_scene_resolver,
        design_rules=design_rules,
    )
    glb_bytes = builder.compile()
    stats = model_takeoff(builder.assembly, source_name=name)
    return glb_bytes, stats, builder.assembly

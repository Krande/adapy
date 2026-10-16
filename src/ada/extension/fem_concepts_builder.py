"""Shared builders turning adapy's in-memory FEA *input* concepts —
point masses, boundary conditions, constraints, and concept loads — into the
``fem_concepts`` glTF-extension payload the viewer's FEM mode renders.

Three producers share this so they emit byte-identical concepts:

* :mod:`ada.visit.scene_handling.scene_from_part` — masses + load
  scenarios on the *design* extension of a CAD/FEM GLB.
* :mod:`ada.visit.scene_handling.scene_from_fem` — boundary conditions
  and constraints on the *simulation* extension.
* the Code Aster ``<name>.adapy_fem.json`` sidecar (write side) — the
  full bundle, so the streaming FEA-result bake can carry the concepts
  into the result GLB's manifest. A ``.rmed`` is a *result* file and
  holds none of these inputs; the sidecar is the only place they
  survive round-tripping through the solver.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada import FEM


def _opt_float(v):
    """float(v), or None when the source left the field unset / unparseable."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_mag(v):
    """(unit-direction, magnitude) of a 3-vector; (None, 0.0) for a ~zero vector."""
    comps = [float(c) for c in v]
    mag = math.sqrt(sum(c * c for c in comps))
    if mag < 1e-12:
        return None, 0.0
    return [c / mag for c in comps], mag


def build_mass_glyphs(part_or_assembly):
    """One MassGlyph per point mass under the part tree (self included)."""
    from ada.extension import fem_concepts_schema as fem_ext

    masses = []
    for subp in part_or_assembly.get_all_subparts(include_self=True):
        for m in getattr(subp, "masses", None) or []:
            # Match where the mass sphere renders: the cog is in the local system of the mass point,
            # moved to the world by its absolute placement. Using the raw cog left the amber overlay
            # offset from the geometry whenever the mass carried a non-identity placement (e.g. a
            # model pipeline positions equipment via placement).
            from ada.api.transforms import to_global_points

            cog = to_global_points(m, m.cog) if getattr(m, "placement", None) is not None else m.cog.copy()
            masses.append(
                fem_ext.MassGlyph(
                    name=m.name,
                    position=[float(cog[0]), float(cog[1]), float(cog[2])],
                    mass=float(m.mass),
                )
            )
    return masses


def resolve_load_glyph(load, factor: float = 1.0, generated_by: str | None = None):
    """Map one concept load to a geometric LoadGlyph (or None). `factor` scales
    the magnitude for a load combination's factored case; `generated_by` names the
    equipment an analysis model resolved the load from."""
    glyph = _load_glyph(load, factor)
    if glyph is not None and generated_by:
        glyph.generated_by = generated_by
    return glyph


def _load_glyph(load, factor: float):
    from ada.extension import fem_concepts_schema as fem_ext
    from ada.fem.concept.loads import (
        LoadConceptAccelerationField,
        LoadConceptLine,
        LoadConceptPoint,
        LoadConceptSurface,
    )

    if isinstance(load, LoadConceptPoint):
        direction, mag = _norm_mag(load.force)
        moment = [float(c) * factor for c in load.moment] if any(load.moment) else None
        return fem_ext.LoadGlyph(
            name=load.name,
            type="point",
            position=[float(c) for c in load.position],
            direction=direction,
            magnitude=mag * factor,
            moment=moment,
        )
    if isinstance(load, LoadConceptLine):
        direction, mag = _norm_mag(load.intensity_start)
        end = [float(c) for c in load.intensity_end]
        if direction is None:
            # Zero at the start (a load rising from nothing): the end gives the direction.
            direction, end_mag = _norm_mag(end)
        else:
            # The end's intensity along the start's direction -- the glyph has one direction.
            end_mag = sum(d * c for d, c in zip(direction, end))
        return fem_ext.LoadGlyph(
            name=load.name,
            type="line",
            position=[float(c) for c in load.start_point],
            end_position=[float(c) for c in load.end_point],
            direction=direction,
            magnitude=mag * factor,
            end_magnitude=end_mag * factor if abs(end_mag - mag) > 1e-9 * max(1.0, mag) else None,
        )
    if isinstance(load, LoadConceptSurface):
        points = None
        if load.plate_ref is not None:
            points = [[float(c) for c in p] for p in load.plate_ref.poly.points3d]
        elif load.points is not None:
            points = [[float(c) for c in p] for p in load.points]
        return fem_ext.LoadGlyph(
            name=load.name,
            type="surface",
            points=points,
            magnitude=float(load.pressure) * factor if load.pressure is not None else None,
        )
    if isinstance(load, LoadConceptAccelerationField):
        direction, mag = _norm_mag(load.acceleration)
        return fem_ext.LoadGlyph(name=load.name, type="accel", direction=direction, magnitude=mag * factor)
    return None


def build_load_scenarios(part_or_assembly):
    """One LoadScenario per load case AND per load combination, each pre-resolved
    to a flat list of LoadGlyphs (combinations apply each factored case's factor
    × the combination's global scale) — so the viewer can cycle and render
    scenario[i].loads directly without superposing client-side."""
    from ada.extension import fem_concepts_schema as fem_ext

    cfem = getattr(part_or_assembly, "concept_fem", None)
    if cfem is None or getattr(cfem, "loads", None) is None:
        return []
    try:
        loads = cfem.loads.get_global_load_concepts()
    except Exception:
        loads = cfem.loads

    generated = _generated_loads(part_or_assembly)

    def case_glyphs(case, factor: float = 1.0):
        out = [g for g in (resolve_load_glyph(ld, factor) for ld in case.loads) if g is not None]
        for equipment, ld in generated.get(case.name, ()):
            g = resolve_load_glyph(ld, factor, generated_by=equipment)
            if g is not None:
                out.append(g)
        return out

    scenarios = []
    for name, case in (loads.load_cases or {}).items():
        glyphs = case_glyphs(case)
        if glyphs:
            scenarios.append(fem_ext.LoadScenario(name=name, kind="case", loads=glyphs))

    for name, comb in (loads.load_case_combinations or {}).items():
        gsf = getattr(comb, "global_scale_factor", 1.0) or 1.0
        glyphs = []
        for fc in comb.load_cases:
            f = (getattr(fc, "factor", 1.0) or 0.0) * gsf
            glyphs.extend(case_glyphs(fc.load_case, f))
        if glyphs:
            scenarios.append(fem_ext.LoadScenario(name=name, kind="combination", loads=glyphs))

    return scenarios


def _equipment_concepts(part_or_assembly):
    """Every part's equipment concepts under the tree (self included). A part unpickled from
    before equipment was read has none."""
    for subp in part_or_assembly.get_all_subparts(include_self=True):
        eq = getattr(subp.concept_fem, "equipment", None)
        if eq:
            yield eq


def _generated_loads(part_or_assembly) -> dict[str, list]:
    """The loads the analysis model resolved its equipment into, by load case name."""
    out: dict[str, list] = {}
    for eq in _equipment_concepts(part_or_assembly):
        for case, pairs in eq.generated_loads.items():
            out.setdefault(case, []).extend(pairs)
    return out


def build_equipment_glyphs(part_or_assembly):
    """One EquipmentGlyph per distinct placement: an equipment set down at the same spot in
    several load cases is one box, listing those cases. Positions are as the masses' are, raw
    model coordinates."""
    from ada.extension import fem_concepts_schema as fem_ext

    glyphs: dict[tuple, fem_ext.EquipmentGlyph] = {}
    for eq in _equipment_concepts(part_or_assembly):
        for placed in eq.placements:
            prism = eq.prisms.get(placed.equipment)
            if prism is None:
                continue
            key = (
                placed.equipment,
                tuple(round(c, 9) for c in placed.origin),
                tuple(round(c, 9) for axis in placed.axes for c in axis),
            )
            glyph = glyphs.get(key)
            if glyph is None:
                lo, hi = prism.local_box()
                glyph = glyphs[key] = fem_ext.EquipmentGlyph(
                    name=prism.name,
                    mass=float(prism.mass),
                    origin=[float(c) for c in placed.origin],
                    axes=[[float(c) for c in axis] for axis in placed.axes],
                    box_min=list(lo),
                    box_max=list(hi),
                    cog=list(placed.to_global(prism.cog)),
                    load_cases=[],
                )
            if placed.load_case and placed.load_case not in glyph.load_cases:
                glyph.load_cases.append(placed.load_case)
    return list(glyphs.values())


#: Points drawn along a curve support, ends included.
_CURVE_SUPPORT_SAMPLES = 5


def build_concept_bc_glyphs(part_or_assembly):
    """One BcGlyph per support defined on the concept model (point, curve and beam-end
    supports), for a model that has no mesh to carry them as FEM boundary conditions -- a
    GeniE workspace's supports, say. Rigid links are not supports and are left out."""
    from ada.extension import fem_concepts_schema as fem_ext

    try:
        constraints = part_or_assembly.concept_fem.constraints.get_part_constraint_concepts()
    except ValueError as e:
        # Two parts naming a support alike: the constraint walk refuses to merge them.
        logger.debug(e)
        return []

    def glyph(c, positions):
        held = [(i + 1, d.constraint_type) for i, d in enumerate(c.dof_constraints) if d.constraint_type != "free"]
        if not held or not positions:
            return None
        kinds = sorted({kind for _, kind in held})
        return fem_ext.BcGlyph(
            name=c.name,
            positions=[[float(v) for v in p] for p in positions],
            dofs=[i for i, _ in held],
            bc_type="+".join(kinds),
        )

    out = []
    for c in constraints.point_constraints.values():
        out.append(glyph(c, [c.position]))
    for c in constraints.beam_end_constraints.values():
        out.append(glyph(c, [c.position]))
    for c in constraints.curve_constraints.values():
        a = [float(v) for v in c.start_pos]
        b = [float(v) for v in c.end_pos]
        n = _CURVE_SUPPORT_SAMPLES
        out.append(glyph(c, [[a[k] + (b[k] - a[k]) * i / (n - 1) for k in range(3)] for i in range(n)]))
    return [g for g in out if g is not None]


def _has_fem_bcs(part_or_assembly) -> bool:
    """Whether a FEM under the tree carries boundary conditions of its own -- then the
    simulation extension draws the supports, and the concept ones would be drawn twice."""
    return any(getattr(p.fem, "bcs", None) for p in part_or_assembly.get_all_subparts(include_self=True))


def build_bc_glyphs(fem: "FEM"):
    """One BcGlyph per boundary condition with resolvable restrained-node
    positions; empty list when the FEM has no usable BCs."""
    from ada import Node
    from ada.extension import fem_concepts_schema as fem_ext

    bcs = []
    for bc in getattr(fem, "bcs", None) or []:
        fset = getattr(bc, "fem_set", None)
        if fset is None:
            continue
        positions = [[float(m.p[0]), float(m.p[1]), float(m.p[2])] for m in fset.members if isinstance(m, Node)]
        if not positions:
            continue
        bcs.append(
            fem_ext.BcGlyph(
                name=bc.name,
                positions=positions,
                # bc.dofs is a 6-slot list with None for unconstrained DOFs (e.g.
                # [None, 2, None, 4, None, 6] for YSYMM) — keep only the constrained ones.
                dofs=[int(d) for d in bc.dofs if d is not None],
                bc_type=str(getattr(bc, "type", "") or "") or None,
            )
        )
    return bcs


def build_constraint_glyphs(fem: "FEM"):
    """One ConstraintGlyph per constraint, carrying the master and slave node
    positions so the viewer can draw the two sides in different colours.

    A constraint's sides are ``FemSet`` or ``Surface``, and a surface may name its
    region through a set, a list of sets, or ``id_refs`` -- ``surface_nodes``
    flattens all of those to nodes, so this doesn't have to care which it got.
    """
    from ada.extension import fem_concepts_schema as fem_ext
    from ada.fem.surfaces import surface_nodes

    def positions(region):
        if region is None:
            return []
        try:
            return [[float(n.p[0]), float(n.p[1]), float(n.p[2])] for n in surface_nodes(region)]
        except Exception as e:
            # A region naming a set the FEM can't resolve shouldn't cost the viewer
            # every other constraint.
            logger.debug(e)
            return []

    constraints = []
    for c in (getattr(fem, "constraints", None) or {}).values():
        masters, slaves = positions(c.m_set), positions(c.s_set)
        if not masters and not slaves:
            continue
        constraints.append(
            fem_ext.ConstraintGlyph(
                name=c.name,
                constraint_type=str(getattr(c, "type", "") or "") or None,
                master_positions=masters,
                slave_positions=slaves,
                dofs=[int(d) for d in (c.dofs or []) if d is not None] or None,
                influence_distance=_opt_float(getattr(c, "influence_distance", None)),
                position_tolerance=_opt_float(getattr(c, "pos_tol", None)),
            )
        )
    return constraints


def build_design_fem_concepts(part_or_assembly):
    """Design-side bundle: masses, load scenarios, equipment and the concept model's
    supports. None when empty.

    A meshed model's BCs live on the simulation extension (see
    :func:`build_sim_fem_concepts`) because they're a property of the FEM; the
    supports a concept model defines are drawn from here only while no FEM under it
    carries BCs of its own, so one support is never drawn twice."""
    from ada.extension import fem_concepts_schema as fem_ext

    masses = build_mass_glyphs(part_or_assembly)
    scenarios = build_load_scenarios(part_or_assembly)
    equipment = build_equipment_glyphs(part_or_assembly)
    bcs = [] if _has_fem_bcs(part_or_assembly) else build_concept_bc_glyphs(part_or_assembly)
    if not masses and not scenarios and not equipment and not bcs:
        return None
    return fem_ext.FemConcepts(
        masses=masses or None,
        bcs=bcs or None,
        scenarios=scenarios or None,
        equipment=equipment or None,
    )


def build_sim_fem_concepts(fem: "FEM"):
    """Simulation-side bundle: boundary conditions and constraints. None when empty."""
    from ada.extension import fem_concepts_schema as fem_ext

    bcs = build_bc_glyphs(fem)
    constraints = build_constraint_glyphs(fem)
    if not bcs and not constraints:
        return None
    return fem_ext.FemConcepts(bcs=bcs or None, constraints=constraints or None)


def _iter_fems(assembly):
    """The assembly's own FEM plus every distinct subpart FEM."""
    root = getattr(assembly, "fem", None)
    if root is not None:
        yield root
    for part in assembly.get_all_parts_in_assembly(True):
        f = getattr(part, "fem", None)
        if f is None or f is root:
            continue
        yield f


def build_combined_fem_concepts(assembly):
    """Full bundle for the sidecar — masses + load scenarios from the part
    tree AND boundary conditions from every FEM under the assembly. Returns
    None when there's nothing to emit.

    This is the union of what the design and simulation GLB producers emit
    separately, collapsed into one object so the FEA-result bake can carry
    every concept through a single sidecar."""
    from ada.extension import fem_concepts_schema as fem_ext

    masses = build_mass_glyphs(assembly)
    scenarios = build_load_scenarios(assembly)
    equipment = build_equipment_glyphs(assembly)
    bcs = []
    constraints = []
    for fem in _iter_fems(assembly):
        bcs.extend(build_bc_glyphs(fem))
        constraints.extend(build_constraint_glyphs(fem))
    if not bcs:
        bcs = build_concept_bc_glyphs(assembly)

    if not masses and not scenarios and not bcs and not constraints and not equipment:
        return None
    return fem_ext.FemConcepts(
        masses=masses or None,
        bcs=bcs or None,
        constraints=constraints or None,
        scenarios=scenarios or None,
        equipment=equipment or None,
    )

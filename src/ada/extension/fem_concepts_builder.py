"""Shared builders turning adapy's in-memory FEA *input* concepts —
point masses, boundary conditions, and concept loads — into the
``fem_concepts`` glTF-extension payload the viewer's FEM mode renders.

Three producers share this so they emit byte-identical concepts:

* :mod:`ada.visit.scene_handling.scene_from_part` — masses + load
  scenarios on the *design* extension of a CAD/FEM GLB.
* :mod:`ada.visit.scene_handling.scene_from_fem` — boundary conditions
  on the *simulation* extension.
* the Code Aster ``<name>.adapy_fem.json`` sidecar (write side) — the
  full bundle, so the streaming FEA-result bake can carry the concepts
  into the result GLB's manifest. A ``.rmed`` is a *result* file and
  holds none of these inputs; the sidecar is the only place they
  survive round-tripping through the solver.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import FEM


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
            cog = m.cog
            masses.append(
                fem_ext.MassGlyph(
                    name=m.name,
                    position=[float(cog[0]), float(cog[1]), float(cog[2])],
                    mass=float(m.mass),
                )
            )
    return masses


def resolve_load_glyph(load, factor: float = 1.0):
    """Map one concept load to a geometric LoadGlyph (or None). `factor` scales
    the magnitude for a load combination's factored case."""
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
        return fem_ext.LoadGlyph(
            name=load.name,
            type="line",
            position=[float(c) for c in load.start_point],
            end_position=[float(c) for c in load.end_point],
            direction=direction,
            magnitude=mag * factor,
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

    scenarios = []
    for name, case in (loads.load_cases or {}).items():
        glyphs = [g for g in (resolve_load_glyph(ld) for ld in case.loads) if g is not None]
        if glyphs:
            scenarios.append(fem_ext.LoadScenario(name=name, kind="case", loads=glyphs))

    for name, comb in (loads.load_case_combinations or {}).items():
        gsf = getattr(comb, "global_scale_factor", 1.0) or 1.0
        glyphs = []
        for fc in comb.load_cases:
            f = (getattr(fc, "factor", 1.0) or 0.0) * gsf
            glyphs.extend(g for g in (resolve_load_glyph(ld, f) for ld in fc.load_case.loads) if g is not None)
        if glyphs:
            scenarios.append(fem_ext.LoadScenario(name=name, kind="combination", loads=glyphs))

    return scenarios


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


def build_design_fem_concepts(part_or_assembly):
    """Design-side bundle: masses + load scenarios. None when empty.

    BCs live on the simulation extension (see :func:`build_sim_fem_concepts`)
    because they're a property of the FEM, not the CAD part."""
    from ada.extension import fem_concepts_schema as fem_ext

    masses = build_mass_glyphs(part_or_assembly)
    scenarios = build_load_scenarios(part_or_assembly)
    if not masses and not scenarios:
        return None
    return fem_ext.FemConcepts(masses=masses or None, scenarios=scenarios or None)


def build_sim_fem_concepts(fem: "FEM"):
    """Simulation-side bundle: boundary conditions. None when empty."""
    from ada.extension import fem_concepts_schema as fem_ext

    bcs = build_bc_glyphs(fem)
    if not bcs:
        return None
    return fem_ext.FemConcepts(bcs=bcs)


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
    bcs = []
    for fem in _iter_fems(assembly):
        bcs.extend(build_bc_glyphs(fem))

    if not masses and not scenarios and not bcs:
        return None
    return fem_ext.FemConcepts(
        masses=masses or None,
        bcs=bcs or None,
        scenarios=scenarios or None,
    )

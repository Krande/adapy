from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import numpy as np

from ada.fem import Load, LoadLine, LoadPressure
from ada.fem.exceptions.model_definition import UnsupportedLoadType

from .names import concept_name

if TYPE_CHECKING:
    from ada.fem import FEM, FemSet
    from ada.fem.steps import Step


def write_load(load: Load) -> str:
    load_str_map = {
        Load.TYPES.GRAVITY: gravity_load_str,
        Load.TYPES.ACC: gravity_load_str,
        Load.TYPES.PRESSURE: pressure_load_str,
        Load.TYPES.LINE: line_load_str,
        Load.TYPES.FORCE: point_load_str,
    }

    load_str_writer = load_str_map.get(load.type, None)

    if load_str_writer is None:
        raise UnsupportedLoadType(f'code_aster writer: load {load.name!r} of type "{load.type}" has no Code_Aster form')

    if getattr(load, "amplitude", None) is not None:
        from ada.fem.formats import conversion_report

        conversion_report.current().omitted(
            STAGE, "Load", load.name, "an amplitude; this writer applies the load in full"
        )
    return load_str_writer(load)


def gravity_load_str(load: Load) -> str:
    """A gravity or acceleration field as ``PESANTEUR``: the magnitude and the direction of the load's acceleration.

    The direction used to be ``(0, 0, 1)`` whatever the load said, so an acceleration along x or y -- a concept
    acceleration field becomes ``|a|`` along a unit ``dof`` -- was applied along z.
    """
    from ada.fem.loads import acceleration_vector

    acc = np.asarray(acceleration_vector(load), dtype=float)
    magnitude = float(np.linalg.norm(acc))
    if magnitude == 0.0:
        raise UnsupportedLoadType(f"code_aster writer: gravity load {load.name!r} has no acceleration")
    direction = ", ".join(repr(float(d)) for d in acc / magnitude)
    group = ""
    if load.fem_set is not None:
        group = f", GROUP_MA='{load.fem_set.name}'"
    return f"""{concept_name(load, "load")} = AFFE_CHAR_MECA(
    MODELE=model, PESANTEUR=_F(DIRECTION=({direction}), GRAVITE={magnitude!r}{group})
)"""


#: Code_Aster's FORCE_NODALE components, dof 1 to 6.
_NODAL_COMPONENTS = ("FX", "FY", "FZ", "MX", "MY", "MZ")


def point_load_str(load: Load) -> str:
    """A point load as ``FORCE_NODALE`` on its node group, global components, moments as ``MX``/``MY``/``MZ``.

    The writer raised on every point load (``NotImplementedError: Load type "force"``). ``FORCE_NODALE`` takes a node
    group (U4.44.01); a load given in a local system is written in global components (``Load.forces_global``).
    """
    from ada.fem import FemSet

    fem_set = load.fem_set
    if fem_set is None or fem_set.type != FemSet.TYPES.NSET:
        raise UnsupportedLoadType(f"code_aster writer: point load {load.name!r} is not on a node set")
    forces = load.forces_global
    comps = ", ".join(f"{c}={float(f)!r}" for c, f in zip(_NODAL_COMPONENTS, forces) if f not in (None, 0, 0.0))
    if not comps:
        raise UnsupportedLoadType(f"code_aster writer: point load {load.name!r} has no non-zero component")
    return f"""{concept_name(load, "load")} = AFFE_CHAR_MECA(
    MODELE=model,
    FORCE_NODALE=_F(GROUP_NO='{fem_set.name}', {comps}),
)"""


#: The factor a pressure magnitude is written with, per surface side.
#:
#: adapy's convention is Abaqus': a positive pressure pushes **into** the face it names, which is
#: along the element's *negative* normal for the positive (SPOS) face.
#:
#: ``FORCE_COQUE``'s ``PRES`` is the same way round. Measured on the 4.0 x 0.5 m, 10 mm strip in
#: cylindrical bending under 1000 Pa (``tests/fem/test_pressure_load_cross_format.py``), whose mesh
#: gmsh winds with every element normal along -z: a positive ``PRES`` moved mid-span to +z, i.e.
#: along the negative normal. So a positive-face pressure is written through unchanged and only a
#: negative-face one is flipped -- the same rule the Sesam BEUSLO writer applies, arrived at from the
#: same Abaqus measurement.
PRESSURE_SIGN = {"positive": +1.0, "negative": -1.0}

#: Surface sides naming an element's negative-normal face, and its positive-normal face. Shared
#: reading with the Sesam and Calculix writers: the Abaqus reader normalises a single-elset shell
#: surface to ``-1``, and a plain element set names no side, which is Abaqus' own default of SPOS.
_NEGATIVE_SIDES = frozenset({-1, "SNEG"})
_POSITIVE_SIDES = frozenset({None, "", 1, "SPOS"})


def _pressure_sign(side) -> float:
    if side in _POSITIVE_SIDES:
        return PRESSURE_SIGN["positive"]
    if side in _NEGATIVE_SIDES:
        return PRESSURE_SIGN["negative"]
    raise UnsupportedLoadType(
        f"a pressure on surface side {side!r} has no Code_Aster FORCE_COQUE form; a shell pressure names "
        f"one of the element's two faces (SPOS / SNEG) and carries its direction in the sign"
    )


def pressure_load_str(load: LoadPressure) -> str:
    """A uniform pressure on shell elements as ``FORCE_COQUE``, over the element sets it acts on.

    Two things here were wrong before and are worth naming, because a deck carrying either analysed:

    ``FORCE_FACE=_F(FY=...)`` is a surface traction along global **Y**, not a pressure. On this
    strip -- loaded normal to its own plane, which is the x-y plane -- it asked Code_Aster for an
    in-plane load, so even had the group resolved, the answer would have been a membrane problem
    rather than plate bending. ``FORCE_COQUE``'s ``PRES`` is the normal pressure on a shell.

    ``GROUP_MA`` was the **surface's** name. A surface is an adapy concept with no counterpart in a
    MED file, which carries mesh groups: the groups written are the element sets, so a deck naming
    the surface stopped at ``<EXCEPTION> <MODELISA7_77>`` -- no such group -- and no pressure had
    ever reached Code_Aster. The element sets behind the surface are named instead, resolved by
    :func:`ada.fem.surfaces.pressure_elsets`, which the Calculix writer resolves its own with too so
    the two decks load the same elements.

    Refused by name rather than approximated: a total-force magnitude (``PRES`` is an intensity), a
    node-based surface or a bare element id (a mesh group is named, not an element), and a pressure
    on anything but a shell -- ``PRES_REP`` is the 3D counterpart and which face of a solid a given
    surface side means is not established here.
    """
    from ada.fem.shapes.definitions import ShellShapes
    from ada.fem.surfaces import pressure_elsets

    distribution = getattr(load, "distribution", None)
    if distribution is not None and distribution != LoadPressure.P_DIST_TYPES.UNIFORM:
        raise UnsupportedLoadType(
            f'a "{distribution}" pressure is not a pressure intensity; Code_Aster FORCE_COQUE takes one'
        )

    surface = getattr(load, "surface", None)
    region = load.fem_set if surface is None else surface
    if region is None:
        raise UnsupportedLoadType(f"pressure load {load.name!r} names neither a surface nor an element set")
    try:
        groups = pressure_elsets(region)
    except ValueError as e:
        raise UnsupportedLoadType(f"pressure load {load.name!r} has no Code_Aster FORCE_COQUE form: {e}") from e

    rows = []
    for fem_set, side in groups:
        for el in fem_set.members:
            el_type = getattr(el, "type", None)
            if el_type is None or not isinstance(el_type, ShellShapes):
                raise UnsupportedLoadType(
                    f"pressure load {load.name!r} acts on a {el_type} element; FORCE_COQUE is a shell load "
                    f"and the solid form (PRES_REP) is not established here"
                )
        pres = _pressure_sign(side) * load.magnitude
        rows.append(f"        _F(GROUP_MA='{fem_set.name}', PRES={pres}),")

    # VERI_NORM='NON' because the group loaded here is the shell model itself, not a skin. The
    # default 'OUI' runs Code_Aster's boundary-orientation check, which wants the named cells to be
    # the connected boundary of something and stops with <MESH3_99> ("les groupes de mailles de bord
    # ne forment pas un ensemble connexe") on a plain shell patch -- measured on the strip. Switching
    # it off also keeps the load's direction tied to the element normals as meshed, rather than to
    # whatever outward orientation the check would have imposed, which is what makes the sign above
    # reproducible.
    return "{0} = AFFE_CHAR_MECA(\n    MODELE=model,\n    VERI_NORM='NON',\n    FORCE_COQUE=(\n{1}\n    ),\n)".format(
        concept_name(load, "load"), "\n".join(rows)
    )


#: The conversion report's stage for this writer.
STAGE = "code_aster writer"


def _is_beam(elem) -> bool:
    from ada.fem.shapes.definitions import LineShapes

    return elem.type == LineShapes.LINE


def _line_load_groups(load: LoadLine):
    """The mesh groups a line load is written over: ``(elsets, nsets)``.

    ``elsets`` are ``(name, elements, q)``, one per distinct intensity of the beam segments that load an element
    uniformly from end to end, for ``FORCE_POUTRE``; ``nsets`` are ``(name, nodes, load)``, one per distinct summed
    six-component nodal load of the other segments (:meth:`LoadLine.summed_beam_nodal_loads`: forces and moments on a
    two-node beam, forces on a shell edge), for ``FORCE_NODALE``. Code_Aster names a group, never an element or node
    (``FORCE_NODALE`` takes ``GROUP_NO`` only, U4.44.01), so each distinct value needs one. The names are a hash of the
    load's name and a counter: deterministic, so the writer that adds the groups to the mesh and the one that writes
    the ``.comm`` agree, and at most 24 characters, so the name map (:mod:`.name_map`) leaves them alone.
    """
    prefix = "L" + hashlib.sha1(load.name.encode("utf-8")).hexdigest()[:8]
    by_q: dict[tuple, list] = {}
    rest = []
    for seg in sorted(load.segments, key=lambda s: s.elem.id):
        if seg.uniform_over_beam_element():
            by_q.setdefault(tuple(float(q) for q in seg.q1), []).append(seg.elem)
        else:
            rest.append(seg)
    by_f: dict[tuple, list] = {}
    for node, f in LoadLine.summed_beam_nodal_loads(rest, _is_beam):
        by_f.setdefault(tuple(float(x) for x in f), []).append(node)
    elsets = [(f"{prefix}_e{i}", els, q) for i, (q, els) in enumerate(by_q.items(), start=1)]
    nsets = [(f"{prefix}_n{i}", nodes, f) for i, (f, nodes) in enumerate(by_f.items(), start=1)]
    return elsets, nsets


def add_line_load_groups(steps: list[Step], fem: FEM) -> list[FemSet]:
    """Add the mesh groups of every line load in ``steps`` to ``fem``, so they reach the MED file; returns them, for
    the caller to remove once the deck is written."""
    from ada.fem import FemSet

    added = []
    for step in steps:
        for load in step.loads:
            if not isinstance(load, LoadLine):
                continue
            elsets, nsets = _line_load_groups(load)
            for name, members, set_type in [(n, m, "elset") for n, m, _ in elsets] + [
                (n, m, "nset") for n, m, _ in nsets
            ]:
                container = fem.elsets if set_type == "elset" else fem.nsets
                if name in container:
                    raise UnsupportedLoadType(f"line load {load.name!r}: the mesh already has a group named {name!r}")
                added.append(fem.sets.add(FemSet(name, members, set_type, parent=fem)))
    return added


def line_load_str(load: LoadLine) -> str:
    """A distributed line load as one ``AFFE_CHAR_MECA``: ``FORCE_POUTRE`` where it is exact, nodal loads elsewhere.

    ``FORCE_POUTRE`` with ``FX``/``FY``/``FZ`` is a force per unit length in global components on ``POU_D_E``
    beams (U4.44.01, v14), constant over the group: exact for a beam element loaded uniformly end to end. A varying
    or partial stretch on a beam is written as ``FORCE_NODALE`` with the consistent nodal forces *and moments* of the
    Euler-Bernoulli element (:meth:`LoadLine.hermite_nodal_loads`), which is what ``POU_D_E`` is, so its nodal
    displacements are the exact ones; a shell edge load as the consistent nodal forces of the (linear or quadratic) edge
    (:meth:`LoadLine.nodal_loads`) -- ``FORCE_ARETE``, Code_Aster's shell edge load, acts on segment cells along the
    edge, which an adapy shell mesh does not have -- exact in the resultant and its moment, reported as a note.

    One ``AFFE_CHAR_MECA`` for the whole load because the static step's ``EXCIT`` names a load by its name.
    """
    from ada.fem.formats import conversion_report

    elsets, nsets = _line_load_groups(load)
    blocks = []
    if elsets:
        rows = "\n".join(f"        _F(GROUP_MA='{n}', FX={q[0]!r}, FY={q[1]!r}, FZ={q[2]!r})," for n, _, q in elsets)
        blocks.append(f"    FORCE_POUTRE=(\n{rows}\n    ),")
    if nsets:
        rows = []
        for n, _, f in nsets:
            comps = ", ".join(f"{c}={v!r}" for c, v in zip(_NODAL_COMPONENTS, f) if v != 0.0 or c == "FX")
            rows.append(f"        _F(GROUP_NO='{n}', {comps}),")
        blocks.append("    FORCE_NODALE=(\n{0}\n    ),".format("\n".join(rows)))
        if any(seg.edge is not None or not _is_beam(seg.elem) for seg in load.segments):
            conversion_report.current().note(
                STAGE,
                "LoadLine",
                load.name,
                "a shell edge load is written as the consistent nodal forces of the edges it acts on, linear or quadratic "
                "(FORCE_NODALE): FORCE_ARETE needs edge cells the mesh does not have",
                n_nodes=sum(len(nodes) for _, nodes, _ in nsets),
            )
    return "{0} = AFFE_CHAR_MECA(\n    MODELE=model,\n{1}\n)".format(concept_name(load, "load"), "\n".join(blocks))

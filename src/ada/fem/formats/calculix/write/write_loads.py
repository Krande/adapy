from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.fem import Load, LoadLine
from ada.fem.exceptions.model_definition import UnsupportedLoadType

if TYPE_CHECKING:
    from .writer import DeckContext

#: The conversion report's stage for this writer.
STAGE = "calculix writer"


def load_str(load: Load, deck: DeckContext) -> str:
    """One load's keyword block. Every kind adapy's FE loads come in has a form here or is refused by name."""
    if load.type in (Load.TYPES.GRAVITY, Load.TYPES.ACC):
        return gravity_load_str(load, deck)
    if load.type == Load.TYPES.PRESSURE:
        return pressure_load_str(load)
    if load.type == Load.TYPES.LINE:
        return line_load_str(load, deck)
    if load.type == Load.TYPES.FORCE:
        return point_load_str(load)
    raise UnsupportedLoadType(f'calculix writer: load {load.name!r} of type "{load.type}" has no Calculix form')


def _report_amplitude(load: Load) -> None:
    from ada.fem.formats import conversion_report

    if getattr(load, "amplitude", None) is not None:
        conversion_report.current().omitted(
            STAGE, "Load", load.name, "an amplitude; this writer writes no *AMPLITUDE, the load is applied in full"
        )


def ccx_number(value: float) -> str:
    """A real as ccx reads it: at most 20 characters. Measured, ccx 2.23: "*ERROR reading *CLOAD" on
    ``5,5,-1.7763568394002505E-15`` (Python's ``repr``, 23 characters); ``%.13E`` is 20 at most, 14 digits."""
    return f"{float(value):.13E}"


def _cload_lines(rows) -> str:
    """``*CLOAD`` data lines from ``(node or set, dof, value)``, zero values left out."""
    return "\n".join(f"{target}, {dof}, {ccx_number(value)}" for target, dof, value in rows if value != 0.0)


def point_load_str(load: Load) -> str:
    """A point load as ``*CLOAD`` on its node set, one line per non-zero component, moments on dofs 4-6.

    The writer raised on every ordinary point load ("Calculix does not accept Loads without reference to a fem_set"),
    so a model with one did not write at all. ``*CLOAD`` takes a node set (CalculiX 2.23 manual, 7.10); a load given in
    a local system is written in global components (``Load.forces_global``).
    """
    from ada.fem import FemSet

    _report_amplitude(load)
    fem_set = load.fem_set
    if fem_set is None or fem_set.type != FemSet.TYPES.NSET:
        raise UnsupportedLoadType(f"calculix writer: point load {load.name!r} is not on a node set; *CLOAD names one")
    forces = load.forces_global
    if forces is None:
        raise UnsupportedLoadType(f"calculix writer: point load {load.name!r} has no global components")
    rows = [(fem_set.name, dof, f) for dof, f in enumerate(forces, start=1)]
    return "** Name: {0}   Type: Concentrated force\n*Cload\n{1}".format(load.name, _cload_lines(rows))


def gravity_load_str(load: Load, deck: DeckContext) -> str:
    """A gravity or acceleration field: ``*DLOAD GRAV`` on the elements CalculiX takes a body force on, and on the
    ``U1`` beams, which take none ("*ERROR in e_c3d_u1: no body forces", ccx 2.23), their weight per unit length
    ``rho A a`` as the consistent nodal loads of an Euler-Bernoulli element (:meth:`LoadLine.hermite_nodal_loads`) --
    exact at the nodes, like the beam's own bending.

    ``GRAV`` is a magnitude and a unit direction (CalculiX 2.23 manual, 7.36), written from the load's acceleration
    vector so a concept acceleration field (``|a|`` along a unit ``dof``) and a ``LoadGravity`` (``-9.81`` along
    ``[0, 0, 1]``) both say which way the weight acts. The writer used to fail on the former and set every gravity
    load's element set to a set it added to the model.
    """
    from ada.fem import FemSet
    from ada.fem.formats.abaqus.write.write_sections import eval_general_properties
    from ada.fem.loads import LineLoadSegment, acceleration_vector

    _report_amplitude(load)
    acc = np.asarray(acceleration_vector(load), dtype=float)
    magnitude = float(np.linalg.norm(acc))
    if magnitude == 0.0:
        return f"** Name: {load.name}   Type: Gravity (zero)"
    direction = acc / magnitude
    blocks = []
    target = None
    if load.fem_set is not None:
        if load.fem_set.type != FemSet.TYPES.ELSET:
            raise UnsupportedLoadType(
                f"calculix writer: gravity load {load.name!r} is on a node set; GRAV takes elements"
            )
        members = load.fem_set.members
        target = load.fem_set.name if all(el.id not in deck.u1_elements for el in members) else None
        if target is None and any(el.id not in deck.u1_elements for el in members):
            raise UnsupportedLoadType(
                f"calculix writer: gravity load {load.name!r} acts on a set mixing U1 beams and other elements"
            )
        u1 = [el for el in members if el.id in deck.u1_elements]
    else:
        target = deck.grav_elset
        u1 = [el for el in deck.part.fem.elements.lines if el.id in deck.u1_elements]
    if target is not None:
        dirs = ", ".join(ccx_number(d) for d in direction)
        blocks.append(f"*Dload\n{target}, GRAV, {ccx_number(magnitude)}, {dirs}")
    if u1:
        segments = []
        for el in sorted(u1, key=lambda e: e.id):
            rho = float(el.fem_sec.material.model.rho)
            area = float(eval_general_properties(el.fem_sec.section).Ax)
            q = tuple(float(x) for x in rho * area * acc)
            segments.append(LineLoadSegment(el, q, q))
        nodal = LoadLine.summed_beam_nodal_loads(segments, lambda el: True)
        rows = [(node.id, dof + 1, f[dof]) for node, f in nodal for dof in range(6)]
        blocks.append("*Cload\n" + _cload_lines(rows))
    return "** Name: {0}   Type: Gravity\n{1}".format(load.name, "\n".join(blocks))


#: CalculiX's ``*DLOAD`` label for a pressure on a shell element.
#:
#: The label carries no face information here, and that is measured rather than assumed: on one S4
#: whose nodes run counter-clockwise in the x-y plane, ``EALL, P, 1000.``, ``EALL, P1, 1000.`` and
#: ``EALL, P2, 1000.`` returned the **bit-identical** displacement field from ccx 2.23. CalculiX
#: expands a shell into a solid before solving, so the face number does not reach the shell's own
#: two sides; the sign of the magnitude is the only thing that can say which way a pressure pushes.
#: See :data:`PRESSURE_SIGN`.
PRESSURE_LABEL = "P"

#: The factor adapy's pressure magnitude is written with, per surface side.
#:
#: adapy's convention is Abaqus': a positive pressure pushes **into** the face it names. Measured on
#: one S4R with its normal along +z, Abaqus 2025 puts ``*Dsload P, 1000.`` at U3 = -3.2004021e-03 on
#: ``SPOS`` and +3.2004021e-03 on ``SNEG`` (the same measurement the Sesam BEUSLO writer's sign flip
#: rests on -- ``ada.fem.formats.sesam.write.write_loads``).
#:
#: CalculiX is the other way round. Measured on one S4 with its normal along +z, three corners
#: clamped, ``EALL, P, 1000.`` from ccx 2.23 moves the free corner to U3 = **+6.02369e-06** -- along
#: the positive normal, i.e. *away* from the positive face. So a positive-face pressure is written
#: negated and a negative-face one as it stands, and a model solved through Calculix then deflects
#: the same way it does through Abaqus, Code_Aster and Sestra instead of the opposite way.
PRESSURE_SIGN = {"positive": -1.0, "negative": +1.0}

#: Surface sides naming the face on an element's negative normal, and on its positive normal. The
#: same two sets the Sesam writer resolves a pressure with, so the two decks read one model
#: identically: the Abaqus reader normalises a single-elset shell surface to ``-1``, and a plain
#: element set names no side at all, which is Abaqus' own default of SPOS.
_NEGATIVE_SIDES = frozenset({-1, "SNEG"})
_POSITIVE_SIDES = frozenset({None, "", 1, "SPOS"})


def _pressure_region(load: Load):
    """The region a pressure acts on: its ``Surface`` if it has one, else its ``FemSet``.

    ``LoadPressure`` (what the Abaqus reader builds from ``*Dsload``) carries a ``Surface`` and no
    ``fem_set``; a bare ``Load`` of type ``pressure`` carries an element set. Both reach this writer.
    """
    surface = getattr(load, "surface", None)
    return load.fem_set if surface is None else surface


def _pressure_sign(side) -> float:
    """The factor for one surface side, or raise -- a side that is not a shell face is not guessed."""
    if side in _POSITIVE_SIDES:
        return PRESSURE_SIGN["positive"]
    if side in _NEGATIVE_SIDES:
        return PRESSURE_SIGN["negative"]
    raise UnsupportedLoadType(
        f"a pressure on surface side {side!r} has no Calculix form; *DLOAD on a shell names one of "
        f"its two faces (SPOS / SNEG), and which way the load pushes is carried by the sign"
    )


def pressure_load_str(load: Load) -> str:
    """A uniform pressure as ``*DLOAD``, one line per element set of the load's region.

    CalculiX takes a pressure on an element set directly, so the set the surface was built over is
    what is named -- a ccx deck carries no ``*SURFACE`` (the writer does not emit one) and the
    element sets are all written out already.

    Verified against ccx 2.23 on the 4.0 x 0.5 m, 10 mm S355 strip in cylindrical bending under
    1000 Pa that the Sesam BEUSLO writer was verified on: three mesh densities converging at second
    order on the closed form ``5 q L^4 / (384 D)``, deflecting the same way Sestra and Abaqus do,
    and a summed reaction of ``q L b``. See ``tests/fem/test_calculix_pressure_load.py``.

    Refused rather than approximated, each by name: a pressure whose magnitude is a total force
    rather than an intensity, a pressure on a node-based surface or a bare element id (``*DLOAD``
    names a set), and a pressure on a non-shell element -- CalculiX numbers a *solid*'s six faces
    individually, and which one a given surface side means is not established here, so writing one
    would be a guess at the face.
    """
    from ada.fem.loads import LoadPressure
    from ada.fem.shapes.definitions import ShellShapes
    from ada.fem.surfaces import pressure_elsets

    distribution = getattr(load, "distribution", None)
    if distribution is not None and distribution != LoadPressure.P_DIST_TYPES.UNIFORM:
        raise UnsupportedLoadType(f'a "{distribution}" pressure is not a pressure intensity; Calculix *DLOAD takes one')

    region = _pressure_region(load)
    if region is None:
        raise UnsupportedLoadType(f"pressure load {load.name!r} names neither a surface nor an element set")
    try:
        groups = pressure_elsets(region)
    except ValueError as e:
        raise UnsupportedLoadType(f"pressure load {load.name!r} has no Calculix *DLOAD form: {e}") from e

    data = []
    for fem_set, side in groups:
        for el in fem_set.members:
            el_type = getattr(el, "type", None)
            if el_type is None or not isinstance(el_type, ShellShapes):
                raise UnsupportedLoadType(
                    f"pressure load {load.name!r} acts on a {el_type} element; Calculix *DLOAD numbers a "
                    f"solid's faces individually and this writer only establishes the shell case"
                )
        data.append(f"{fem_set.name}, {PRESSURE_LABEL}, {_pressure_sign(side) * load.magnitude}")

    return "** Name: {0}   Type: Pressure\n*Dload\n{1}".format(load.name, "\n".join(data))


def line_load_str(load: LoadLine, deck: DeckContext) -> str:
    """A distributed line load as ``*CLOAD``: consistent nodal forces and moments on a ``U1`` beam, consistent nodal
    forces elsewhere.

    CalculiX 2.23 has no line load this writer could use instead -- measured on a 4 m U1 beam: ``*DLOAD`` with ``PZ``
    stops ccx at "*ERROR reading *DLOAD" (there is no load in global components), and ``P1`` / ``P2`` (along the
    section's own axes) are read but load nothing, the displacements coming out 0. On a ``U1`` element, which bends as
    an Euler-Bernoulli beam, the load is shared by its cubic shape functions with the end moments that go with them
    (:meth:`LoadLine.hermite_nodal_loads`), so the nodal displacements are the exact ones; forces alone (what this
    wrote before) leave a simply supported beam's mid-span short by ``0.8 (h / L)^2``. A shell edge or an expanded
    beam takes the forces of a linear element (:meth:`LoadLine.nodal_loads`): exact in the resultant and its moment,
    reported as a note.
    """
    from ada.fem.formats import conversion_report

    nodal = LoadLine.summed_beam_nodal_loads(load.segments, lambda el: el.id in deck.u1_elements)
    rows = [(node.id, dof + 1, f[dof]) for node, f in nodal for dof in range(6)]
    if any(seg.edge is not None or seg.elem.id not in deck.u1_elements for seg in load.segments):
        conversion_report.current().note(
            STAGE,
            "LoadLine",
            load.name,
            "a line load on a shell edge or an expanded beam is written as the consistent nodal forces of the elements "
            "it acts on, linear or quadratic (*CLOAD): CalculiX has no line load in global components and this writer "
            "has no shell edge form",
            n_nodes=len(nodal),
        )
    return "** Name: {0}   Type: Line load as nodal loads\n*Cload\n{1}".format(load.name, _cload_lines(rows))

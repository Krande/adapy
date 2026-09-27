"""Sestra solves a uniform pressure written as BEUSLO, and lands on the closed form.

This is the test that establishes the BEUSLO layout. It is not read off a manual -- none ships
with the installed Sestra -- but off Sestra V11.3-00 itself: the field names come from its own
``BeusloReader`` accessors in ``Bin/DataAccess.dll``, the field order from DNV's ``Beuslo`` SIF
type in ``Bin/DNV.Sesam.SifApi.DataTypes.dll``, and every value in it from solving this strip.
Skips, rather than passes, where Sestra is not installed: an unverified record layout is not
something to report as green.

The structure is the ``verification/genie_vs_abaqus`` plate case's: a 4.0 x 0.5 m, 10 mm S355
strip, simply supported on the two short edges and held in cylindrical bending by ``u2 = 0,
ur1 = 0`` on both long edges, under 1000 Pa. That case measured Sestra on this very mesh with
the *equivalent nodal loads*, because the Sesam writer had no distributed-load record at all;
:data:`NODAL_LOAD_REFERENCE` is what it measured, and a BEUSLO deck has to reproduce it,
because for a 4-node bilinear quad ``integral(N_i) dA = A / 4`` exactly -- so the consistent
load vector of a uniform pressure *is* ``q A / 4`` per node, and BEUSLO's own integration
either equals it or is wrong.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, Load, Surface
from ada.fem.loads import LoadPressure
from ada.fem.shapes.definitions import ShellShapes
from ada.fem.steps import StepImplicitStatic
from ada.materials.metals import CarbonSteel

#: Strip span and width, metres. The long edges are the ones held in cylindrical bending.
LENGTH = 4.0
WIDTH = 0.5

#: Plate thickness, metres. ``L / t = 400``: thin, so transverse shear sits at the 1e-05 level,
#: far below the discretisation this measures.
THICKNESS = 0.010

#: Uniform pressure, pascals. Positive, and applied to the face on the elements' positive
#: normal, which is the sign Abaqus' ``*Dsload P`` gives on ``SPOS``.
PRESSURE = 1000.0

#: S355 as adapy's ``CarbonSteel`` gives it: E = 210 GPa, nu = 0.3.
E = 210.0e9
NU = 0.3

#: Element seed sizes, metres -- 32 / 64 / 128 elements per span, a factor of two apart so an
#: order can be read off the three without assuming one.
MESH_SIZES = (0.125, 0.0625, 0.03125)

#: Mid-span ``u3``, metres, that Sestra V11.3-00 returned for this strip at
#: :data:`MESH_SIZES` when the same pressure was applied as the exact consistent *nodal* load
#: (``verification/genie_vs_abaqus``, which had to do it that way because nothing wrote a
#: distributed load). A ``.SIN`` stores nodal results single precision, so these carry about
#: seven significant digits; they are compared **exactly** all the same, because a BEUSLO deck
#: and a consistent-nodal-load deck differ only in how the same load vector was spelled, so
#: their solutions are the same floats, not merely close ones. Measured: they are.
#:
#: These are **negative** because that nodal load ran along global -z: it was applied as
#: ``-PRESSURE * area`` on ``dof=[0, 0, 1]``, on the stated grounds that -z was "against the
#: plate's +z normal". It is not -- gmsh winds every element of this strip with its normal along
#: **-z** -- so -z is along the normal, which is the face on the element's *negative* side. A
#: positive pressure on the **positive** face therefore reproduces these numbers negated, and it is
#: the negative face that reproduces them as they stand. Nothing about the numbers changed when
#: that was found; only which face they belong to, which the comparison below now says explicitly.
#: See ``ada.fem.formats.sesam.write.write_loads._pressure_side_and_sign`` for how it was settled.
NODAL_LOAD_REFERENCE = {
    0.125: -0.1731979101896286,
    0.0625: -0.17329947650432587,
    0.03125: -0.17332486808300018,
}

#: What the strip's total load has to be: ``q L b``, newtons. Sestra's own summed reaction is
#: checked against it, which is the one scalar that says the *whole* pressure arrived -- a deck
#: short by one element's load moves the mid-span deflection by far less than the
#: discretisation residual, and moves this by that element's share exactly.
TOTAL_LOAD = PRESSURE * LENGTH * WIDTH

#: Abaqus 2025 on one S4R whose nodes run counter-clockwise in x-y (so its normal is +z), three
#: corners clamped, ``*Dsload P, 1000.``: the free corner's U3 on the ``SPOS`` face and on the
#: ``SNEG`` face. Equal and opposite -- a positive pressure pushes *into* the face it names --
#: and the reason a negative-face pressure has to reach BEUSLO with its sign flipped.
ABAQUS_DSLOAD_SPOS_U3 = -3.2004021e-03
ABAQUS_DSLOAD_SNEG_U3 = +3.2004021e-03


def _sestra_exe():
    from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

    try:
        return get_sestra_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_sestra_exe() is None, reason="Sestra is not installed")


def closed_form_deflection() -> float:
    """``5 q L^4 / (384 D)`` with ``D = E t^3 / (12 (1 - nu^2))``: a strip in cylindrical
    bending, which is what the long-edge constraint makes this one."""
    d = E * THICKNESS**3 / (12.0 * (1.0 - NU**2))
    return 5.0 * PRESSURE * LENGTH**4 / (384.0 * d)


def _strip(mesh_size: float, *, negative_face: bool = False) -> tuple[ada.Assembly, list]:
    """The strip at one seed, with the pressure on one face or the other.

    ``negative_face`` puts the load on a ``Surface`` with ``el_face_index = -1`` (what the
    Abaqus reader normalises ``SNEG`` to) instead of on the plate's element set, which names no
    side and is therefore the positive face.
    """
    mat = ada.Material("S355", CarbonSteel("S355"))
    outline = [(0.0, 0.0), (LENGTH, 0.0), (LENGTH, WIDTH), (0.0, WIDTH)]
    part = ada.Part("Strip") / [ada.Plate("strip", outline, THICKNESS, mat=mat)]
    assembly = ada.Assembly("StripSite") / part
    # "line" is bm_repr; the plate is a shell. Quadrilateral, because the closed form is for a
    # plate and FQUS is the element the reference numbers were measured on.
    part.fem = part.to_fem_obj(mesh_size, "line", use_quads=True, interactive=False)
    fem = part.fem

    tol = 1e-09
    edges = (
        ("SS_X0", [n for n in fem.nodes if abs(n.x) < tol], (1, 2, 3)),
        ("SS_X1", [n for n in fem.nodes if abs(n.x - LENGTH) < tol], (2, 3)),
        ("CYL", [n for n in fem.nodes if abs(n.y) < tol or abs(n.y - WIDTH) < tol], (2, 4)),
    )
    for name, nodes, dofs in edges:
        assert nodes, f"support set {name} matched no node at seed {mesh_size}"
        fem_set = fem.add_set(FemSet(name, sorted(nodes, key=lambda n: n.id), FemSet.TYPES.NSET, parent=fem))
        fem.add_bc(Bc(name, fem_set, list(dofs)))

    shells = sorted((el for el in fem.elements if isinstance(el.type, ShellShapes)), key=lambda el: el.id)
    assert shells, "the strip meshed with no shell elements, so there is no surface to load"
    elset = fem.add_set(FemSet("PLATE_SHELLS", shells, FemSet.TYPES.ELSET, parent=fem))

    step = assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    if negative_face:
        surface = fem.add_surface(Surface("PLATE_SNEG", Surface.TYPES.ELEMENT, elset, el_face_index=-1, parent=fem))
        step.add_load(LoadPressure("q", PRESSURE, surface))
    else:
        step.add_load(Load("q", Load.TYPES.PRESSURE, PRESSURE, fem_set=elset))
    return assembly, shells


#: Warnings Sestra V11.3-00 raises for a BEUSLO field it will not honour. Both are measured:
#: LAYER != 0 and INTNO != 0 each buy one of these and are then ignored, so the numbers below
#: cannot see either mistake. Asserting their absence is what makes them visible here.
UNHONOURED_FIELD_WARNINGS = (
    "Layered elements is not supported in this version",
    "Non-default integration rule (INTNO) specified",
)


def _solve(assembly, name: str, scratch_dir) -> pathlib.Path:
    assembly.to_fem(name, "sesam", scratch_dir=scratch_dir, overwrite=True, execute=True)
    run_dir = pathlib.Path(scratch_dir) / name
    mlg = (run_dir / "SESTRA.MLG").read_text(errors="replace")
    # Checked rather than trusted: adapy shells out through a batch file and does not surface a
    # non-zero solver exit, and a pressure that reached the deck as nothing used to complete
    # "successfully" on an unloaded structure.
    assert "Execution completed successfully" in mlg, mlg[-2000:]
    for warning in UNHONOURED_FIELD_WARNINGS:
        assert warning not in mlg, f"the deck asks Sestra for something it drops: {warning!r}"
    return run_dir


def _beuslo_records(deck: pathlib.Path) -> list[list[float]]:
    """The ``ELNO NDOF INTNO SIDE`` row of every BEUSLO record in the deck."""
    lines = deck.read_text().splitlines()
    return [[float(x) for x in lines[i + 1].split()] for i, ln in enumerate(lines) if ln.startswith("BEUSLO")]


def _mid_span_u3_and_reaction(sin_path: pathlib.Path) -> tuple[float, tuple[float, float, float]]:
    """Mid-span centreline ``u3`` and the summed support reaction, out of a ``.SIN``.

    Sampled by position, not by node id: the id depends on the mesher and the reference numbers
    were measured on another branch's run of it.
    """
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    result = read_sin_file(sin_path)
    grouped = result.get_results_grouped_by_field_value()
    assert "REACTION-FORCE" in grouped, f"no reaction field in the result; it holds {sorted(grouped)}"
    reactions = np.asarray(grouped["REACTION-FORCE"][0].values, dtype=float)
    total = reactions[:, 1:4].sum(axis=0)

    field = next(f for f in result.results if f.name == "sesam.nodes.displacement")
    coords = np.asarray(result.mesh.nodes.coords, dtype=float)
    offset = np.abs(coords[:, 0] - LENGTH / 2) + np.abs(coords[:, 1] - WIDTH / 2)
    index = int(np.argmin(offset))
    assert offset[index] < 1e-09, "the mesh has no node at mid-span on the centreline"
    # Columns are [node id, ALL (magnitude), X, Y, Z, RX, RY, RZ].
    u3 = float(np.asarray(field.values)[index][4])
    return u3, (float(total[0]), float(total[1]), float(total[2]))


def test_a_pressure_reaches_sestra_as_the_consistent_load(tmp_path):
    """Three meshes: the deck's own BEUSLO records, the reference deflections, the reaction
    total, and second-order convergence on the closed form."""
    measured = {}
    for mesh_size in MESH_SIZES:
        assembly, shells = _strip(mesh_size)
        name = "beuslo_" + str(mesh_size).replace(".", "p")
        run_dir = _solve(assembly, name, tmp_path)

        rows = _beuslo_records(run_dir / f"{name}T1.FEM")
        assert len(rows) == len(shells), "one BEUSLO per shell element, or the load went nowhere"
        # NDOF is the node count and SIDE the positive face, on every record.
        assert {row[1] for row in rows} == {4.0}
        assert {row[3] for row in rows} == {1.0}

        u3, reaction = _mid_span_u3_and_reaction(run_dir / f"{name}R1.SIN")
        measured[mesh_size] = u3

        # The reaction is single-precision in the SIN, so a relative tolerance, not equality --
        # and 1e-07 is five orders tighter than one missing element's share (1/128 at the
        # coarsest mesh), which is what this is here to catch.
        assert reaction[2] == pytest.approx(-TOTAL_LOAD, rel=1e-07), f"q L b at seed {mesh_size}"
        assert reaction[0] == pytest.approx(0.0, abs=1e-06)
        assert reaction[1] == pytest.approx(0.0, abs=1e-06)
        assert u3 > 0.0, (
            "a positive pressure on the positive face pushes into that face, and this mesh's "
            "element normals are -z, so the positive face looks down and the strip moves up"
        )

    # Exactly, not approximately: a BEUSLO deck and a consistent-nodal-load deck are the same
    # load vector spelled two ways, so they are the same floats. If this ever drifts, BEUSLO's
    # own integration is not the consistent load and the docstring above is wrong.
    assert measured == {h: -u for h, u in NODAL_LOAD_REFERENCE.items()}

    coarse, medium, fine = (measured[h] for h in MESH_SIZES)
    ratio = (medium - coarse) / (fine - medium)
    assert ratio == pytest.approx(4.0, abs=0.01), f"second order on a halved mesh, got {ratio}"
    richardson = fine + (fine - medium) / (ratio - 1.0)
    assert abs(richardson) == pytest.approx(closed_form_deflection(), rel=1e-06)


def test_a_pressure_on_the_negative_face_pushes_the_other_way(tmp_path):
    """The sign convention, on both faces of the shell.

    Abaqus is the reference: a positive ``*Dsload P`` pushes into the face it names, measured as
    :data:`ABAQUS_DSLOAD_SPOS_U3` / :data:`ABAQUS_DSLOAD_SNEG_U3` on one S4R. BEUSLO cannot say
    that with SIDE -- Sestra computes the load in the mid-plane and returns the identical result
    for SIDE 1, 2 and 3 -- so the writer flips the intensity instead, and this is what checks
    that the flip is there and is exactly a flip.
    """
    assert ABAQUS_DSLOAD_SNEG_U3 == -ABAQUS_DSLOAD_SPOS_U3, "the Abaqus measurement this mirrors"

    mesh_size = MESH_SIZES[0]
    positive, _ = _strip(mesh_size)
    run_pos = _solve(positive, "face_pos", tmp_path)
    u3_pos, reaction_pos = _mid_span_u3_and_reaction(run_pos / "face_posR1.SIN")

    negative, _ = _strip(mesh_size, negative_face=True)
    run_neg = _solve(negative, "face_neg", tmp_path)
    u3_neg, reaction_neg = _mid_span_u3_and_reaction(run_neg / "face_negR1.SIN")

    # The record says which face the model meant, and carries the direction in its sign.
    pos_rows = _beuslo_records(run_pos / "face_posT1.FEM")
    neg_rows = _beuslo_records(run_neg / "face_negT1.FEM")
    assert {row[3] for row in pos_rows} == {1.0}
    assert {row[3] for row in neg_rows} == {2.0}

    assert u3_pos == -NODAL_LOAD_REFERENCE[mesh_size]
    assert u3_neg == -u3_pos, "the same magnitude the other way, not merely a different number"
    assert reaction_pos[2] == pytest.approx(-TOTAL_LOAD, rel=1e-07)
    assert reaction_neg[2] == pytest.approx(TOTAL_LOAD, rel=1e-07)

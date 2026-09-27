"""``*TIE`` as BLDEP, and the end of ``NotImplementedError`` in the constraint writer.

``bldep_records`` used to raise ``NotImplementedError('Constraint type "tie" is not yet
supported')``. It is reached from ``writer.to_fem`` *before* a byte is written, so an 81 MB
deck that had already been read — and whose every other construct the writer could emit — was
thrown away over one ``*Tie`` block of two lines.

What replaces it is not "skip and warn". Sesam expresses a tie the way Abaqus means it: the
secondary node is projected onto the main **facet** it lands on and interpolated between that
facet's nodes, written as one BLDEP record per facet node for Sesam to sum. A main surface with
no facet to project onto -- a node set -- falls back to a rigid arm to the nearest main node,
which is an approximation and is reported as one with the distance distribution that sizes it.
A constraint type Sesam genuinely cannot express is reported and skipped, which is the case the
old exception was covering for.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pytest

import ada
from ada.api.containers import Nodes
from ada.fem import FEM, Constraint, Elem, FemSet, Surface
from ada.fem.common import LinDep
from ada.fem.constraints import ALL_DOFS
from ada.fem.containers import FemElements
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.write.write_bcs import bnbcd_str
from ada.fem.formats.sesam.write.write_constraints import (
    BldepRecord,
    _main_surface_spacing,
    _max_facet_half_diagonal,
    _rigid_terms,
    bldep_records,
    shell2solid_records,
    tie_records,
)
from ada.fem.formats.sesam.write.writer import node_dofs
from ada.fem.shapes.definitions import ShellShapes, SolidShapes
from ada.fem.surfaces import surface_facets


def _hex_nodes(z0: float, z1: float, first_id: int) -> list[ada.Node]:
    corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
    return [
        ada.Node((x, y, z), first_id + i + 4 * layer)
        for layer, z in enumerate((z0, z1))
        for i, (x, y) in enumerate(corners)
    ]


def _tie_fem(
    *,
    main_is_shell: bool = True,
    secondary_offset: float = 0.0,
    secondary_gaps: tuple[float, float, float, float] | None = None,
    pos_tol: float | None = None,
    metadata: dict | None = None,
    dofs=None,
) -> FEM:
    """A shell quad and a HEX8 sharing no nodes, joined by a ``TIE``.

    The shell sits at ``z = 1`` over the solid's top face (``z = 1 - secondary_offset``), so
    every solid top node has a shell node directly above it. ``main_is_shell`` puts the shell
    on the independent (``m_set``) side, which is the physically sound direction — the
    dependent solid nodes have no rotations of their own and get them from the shell's arm.

    ``secondary_gaps`` drops the four solid top nodes by *different* amounts, so a test that
    cares about the shape of the distance distribution has one to look at.
    """
    shell = [
        ada.Node((0, 0, 1.0), 1),
        ada.Node((1, 0, 1.0), 2),
        ada.Node((1, 1, 1.0), 3),
        ada.Node((0, 1, 1.0), 4),
    ]
    solid = _hex_nodes(0.0, 1.0 - secondary_offset, 11)
    if secondary_gaps is not None:
        corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
        solid = solid[:4] + [
            ada.Node((x, y, 1.0 - gap), 15 + i) for i, ((x, y), gap) in enumerate(zip(corners, secondary_gaps))
        ]
    fem = FEM(
        "tie",
        nodes=Nodes(shell + solid),
        elements=FemElements([Elem(1, shell, ShellShapes.QUAD), Elem(2, solid, SolidShapes.HEX8)]),
    )
    shell_set = fem.add_set(FemSet("shell_face", [fem.nodes.from_id(i) for i in (1, 2, 3, 4)], "nset"))
    solid_set = fem.add_set(FemSet("solid_face", [fem.nodes.from_id(i) for i in (15, 16, 17, 18)], "nset"))

    main, secondary = (shell_set, solid_set) if main_is_shell else (solid_set, shell_set)
    m = Surface("main_surf", Surface.TYPES.NODE, main, parent=fem)
    s = Surface("sec_surf", Surface.TYPES.NODE, secondary, parent=fem)
    fem.add_constraint(
        Constraint(
            "T1",
            Constraint.TYPES.TIE,
            m,
            s,
            dofs=dofs,
            pos_tol=pos_tol,
            metadata=dict(metadata or {}),
            parent=fem,
        )
    )
    return fem


def _shell_only_tie_fem(metadata: dict | None = None) -> FEM:
    """Two shell quads, one above the other: both sides carry six dofs, so rotations tie."""
    lower = [ada.Node((0, 0, 0.0), 1), ada.Node((1, 0, 0.0), 2), ada.Node((1, 1, 0.0), 3), ada.Node((0, 1, 0.0), 4)]
    upper = [ada.Node((0, 0, 0.1), 5), ada.Node((1, 0, 0.1), 6), ada.Node((1, 1, 0.1), 7), ada.Node((0, 1, 0.1), 8)]
    fem = FEM(
        "shell_tie",
        nodes=Nodes(lower + upper),
        elements=FemElements([Elem(1, lower, ShellShapes.QUAD), Elem(2, upper, ShellShapes.QUAD)]),
    )
    m = Surface("main_surf", Surface.TYPES.NODE, fem.add_set(FemSet("lo", lower, "nset")), parent=fem)
    s = Surface("sec_surf", Surface.TYPES.NODE, fem.add_set(FemSet("up", upper, "nset")), parent=fem)
    fem.add_constraint(Constraint("T1", Constraint.TYPES.TIE, m, s, metadata=dict(metadata or {}), parent=fem))
    return fem


def _constraint(fem: FEM) -> Constraint:
    return next(iter(fem.constraints.values()))


def _finding(report, kind: str, keyword: str):
    matches = [f for f in report.of_kind(kind) if f.keyword == keyword]
    assert len(matches) == 1, f"expected exactly one {kind} {keyword} finding, got {[f.one_line() for f in matches]}"
    return matches[0]


# ── the crash itself ─────────────────────────────────────────────────────────────


def test_a_tie_no_longer_raises_and_produces_records():
    """The reported crash, as a test: ``NotImplementedError: Constraint type "tie" is not
    yet supported``, raised after the whole deck had been read."""
    fem = _tie_fem()
    records = bldep_records(fem, node_dofs(fem))
    assert [r.slave for r in records] == [15, 16, 17, 18]


def test_an_unsupported_constraint_type_is_reported_and_skipped_not_raised():
    """The other half: a type Sesam genuinely cannot express costs the caller that
    constraint, not the deck. It has to be *named*, not dropped in silence."""
    fem = _tie_fem()
    _constraint(fem)._con_type = Constraint.TYPES.MPC

    with conversion_report.collect() as report:
        records = bldep_records(fem, node_dofs(fem))

    assert records == []
    # Filed against the adapy construct ("Constraint"), which is where ``bldep_records`` files
    # every type it has no form for, and named in the reason.
    finding = _finding(report, conversion_report.OMITTED, "Constraint")
    assert finding.subject == "T1"
    assert "mpc" in finding.reason


# ── sides and pairing ────────────────────────────────────────────────────────────
#
# Everything down to "the facet interpolation" below uses a ``NODE``-type main surface,
# which has no facet to project onto, so it exercises the **nearest-node fallback** --
# the behaviour these tests pinned before the interpolation existed, and which must keep
# working unchanged for a main surface that names only nodes.


def test_the_dependents_are_the_secondary_surface_and_the_masters_the_main_surface():
    """``s_set`` is the dependent (Abaqus *secondary*) side and ``m_set`` the independent
    (*main*) one — the same meaning they carry for COUPLING and SHELL2SOLID. Getting this
    backwards writes a model that runs and is wrong, so it is pinned on ids, not counts."""
    fem = _tie_fem()
    records = tie_records(_constraint(fem), node_dofs(fem))

    assert sorted(r.slave for r in records) == [15, 16, 17, 18], "solid top face depends"
    assert sorted({r.master for r in records}) == [1, 2, 3, 4], "shell face is independent"


def test_each_dependent_is_tied_to_the_nearest_main_node_with_the_rigid_arm():
    fem = _tie_fem()
    records = {r.slave: r for r in tie_records(_constraint(fem), node_dofs(fem))}

    # node 15 sits at (0,0,1) under shell node 1, 16 under 2, and so on.
    assert {s: r.master for s, r in records.items()} == {15: 1, 16: 2, 17: 3, 18: 4}
    for slave, record in records.items():
        master = fem.nodes.from_id(record.master)
        expected = tuple(LinDep(master.p, fem.nodes.from_id(slave).p).to_integer_list())
        assert record.terms == expected


# ── the cutoff ───────────────────────────────────────────────────────────────────


def test_a_dependent_beyond_the_cutoff_is_left_untied_and_named():
    """A secondary node farther from the nearest main node than ``pos_tol`` plus one
    main-surface edge could not have been tied by Abaqus either. It is left out — and the
    finding names it, because a node silently dropped from a tie is a free-floating mesh."""
    fem = _tie_fem()
    stray = ada.Node((0, 0, 40.0), 19)
    fem.nodes.add(stray)
    fem.sets.get_nset_from_name("solid_face").add_members([stray])

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert 19 not in {r.slave for r in records}
    assert sorted(r.slave for r in records) == [15, 16, 17, 18]
    omitted = _finding(report, conversion_report.OMITTED, "*TIE")
    assert omitted.count == 1
    assert omitted.details["first_nodes"] == [19]


def test_the_cutoff_is_the_position_tolerance_plus_one_main_surface_edge():
    """Abaqus' position tolerance is a distance to the main *surface*; BLDEP can only name a
    node, and a secondary node lying exactly on a facet is still up to one edge from the
    nearest node of it. A node 1.4 away from its nearest main node is inside a cutoff of
    0.5 + 1 (the shell quad's diagonal spacing is sqrt(2)) but outside one of 0 + 1."""
    spacing = _main_surface_spacing(
        np.array([[0, 0, 1.0], [1, 0, 1.0], [1, 1, 1.0], [0, 1, 1.0]]),
        np.array([1, 2, 3, 4], dtype=np.int64),
    )
    assert spacing == pytest.approx(1.0), "nearest-neighbour spacing of a unit quad is its edge"

    fem = _tie_fem(secondary_offset=1.4)  # solid top face drops to z = -0.4
    assert tie_records(_constraint(fem), node_dofs(fem)) == [], "1.4 > 0 + 1"

    fem = _tie_fem(secondary_offset=1.4, pos_tol=0.5)
    assert len(tie_records(_constraint(fem), node_dofs(fem))) == 4, "1.4 <= 0.5 + 1"


def test_a_single_main_node_has_no_spacing_to_measure_and_excludes_nothing():
    assert _main_surface_spacing(np.array([[0.0, 0.0, 0.0]]), np.array([7], dtype=np.int64)) == float("inf")


# ── the measure ──────────────────────────────────────────────────────────────────


def test_the_tie_finding_carries_the_distance_distribution():
    """An approximation nobody can size is a guess. The four gaps are deliberately *unequal*
    — 0.1, 0.2, 0.3, 0.9 — so max, p95 and mean are three different numbers and a finding
    that reported the same statistic three times could not pass. They are recomputed here
    from the coordinates rather than copied from the writer."""
    fem = _tie_fem(metadata={"adjust": "YES"}, secondary_gaps=(0.1, 0.2, 0.3, 0.9))

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    finding = _finding(report, conversion_report.APPROXIMATED, "*TIE")
    distances = np.array(
        [float(np.linalg.norm(fem.nodes.from_id(r.slave).p - fem.nodes.from_id(r.master).p)) for r in records]
    )
    assert sorted(distances) == pytest.approx([0.1, 0.2, 0.3, 0.9]), "the fixture must spread them"
    assert finding.details["n_tied"] == 4
    assert finding.details["n_secondary_nodes"] == 4
    assert finding.details["n_outside_cutoff"] == 0
    assert finding.details["dist_max"] == pytest.approx(0.9)
    assert finding.details["dist_p95"] == pytest.approx(np.percentile(distances, 95))
    assert finding.details["dist_mean"] == pytest.approx(0.375)
    assert finding.details["adjust"] == "yes", "case-normalised, and reported: it is not honoured"
    assert finding.details["cutoff_slack"] == pytest.approx(1.0)
    assert finding.details["cutoff_rule"] == "max nearest-neighbour node spacing", "a NODE surface"


def test_two_ties_each_keep_their_own_measure():
    """Findings deduplicate on (kind, stage, keyword, reason) and merge details first-wins, so
    a reason shared by every tie would collapse them and throw the second one's distances
    away. Each tie's numbers have to survive."""
    near = _tie_fem(secondary_offset=0.25)
    far = _tie_fem(secondary_offset=0.5)
    _constraint(far)._name = "T2"

    with conversion_report.collect() as report:
        tie_records(_constraint(near), node_dofs(near))
        tie_records(_constraint(far), node_dofs(far))

    ties = [f for f in report.of_kind(conversion_report.APPROXIMATED) if f.keyword == "*TIE"]
    assert len(ties) == 2, [f.one_line() for f in ties]
    assert sorted(f.details["dist_max"] for f in ties) == pytest.approx([0.25, 0.5])


def test_two_ties_each_report_their_own_cutoff_when_they_drop_nodes():
    """The same first-wins merge bites the *omission* too, and there it is worse than a lost
    statistic: the finding tells the engineer which nodes were dropped and how far away the
    cutoff was, and a shared reason gave the second tie's nodes the first tie's cutoff."""
    coarse = _tie_fem()  # unit shell quad: cutoff 1.0
    fine = _tie_fem()
    for nid, p in ((1, (0, 0, 1.0)), (2, (0.1, 0, 1.0)), (3, (0.1, 0.1, 1.0)), (4, (0, 0.1, 1.0))):
        fine.nodes.from_id(nid).p = np.array(p, dtype=float)  # a 0.1 quad: cutoff 0.1
    _constraint(fine)._name = "T2"

    with conversion_report.collect() as report:
        for fem in (coarse, fine):
            stray = ada.Node((0, 0, 40.0), 19)
            fem.nodes.add(stray)
            fem.sets.get_nset_from_name("solid_face").add_members([stray])
            tie_records(_constraint(fem), node_dofs(fem))

    dropped = [f for f in report.of_kind(conversion_report.OMITTED) if f.keyword == "*TIE"]
    assert sorted(f.details["cutoff"] for f in dropped) == pytest.approx([0.1, 1.0])


# ── dof logic ────────────────────────────────────────────────────────────────────


def test_a_solid_main_surface_writes_translation_glue_that_bnbcd_accepts():
    """Turn the tie round and the independent nodes are solid nodes with no rotations. The
    nine-term arm would name master dofs 4-6 and ``bnbcd_str`` would reject the writer's own
    output; three identity terms are exact for the translations and valid."""
    fem = _tie_fem(main_is_shell=False)
    ndofs = node_dofs(fem)

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), ndofs)

    assert [r.terms for r in records] == [((1, 1, 1.0), (2, 2, 1.0), (3, 3, 1.0))] * 4
    assert _finding(report, conversion_report.APPROXIMATED, "*TIE").details["n_translation_only"] == 4
    bnbcd_str([fem], records, None, ndofs)  # must not raise


def test_rotations_are_tied_between_two_six_dof_meshes():
    fem = _shell_only_tie_fem()
    records = tie_records(_constraint(fem), node_dofs(fem))

    assert len(records) == 4
    for record in records:
        assert record.slave_dofs == (1, 2, 3, 4, 5, 6)
        assert record.terms[-3:] == ((4, 4, 1.0), (5, 5, 1.0), (6, 6, 1.0))


def test_no_rotation_leaves_the_rotations_free():
    """``*Tie, ..., no rotation`` ties the translations only."""
    fem = _shell_only_tie_fem(metadata={"no_rotation": True})

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    for record in records:
        assert record.slave_dofs == (1, 2, 3)
    assert _finding(report, conversion_report.APPROXIMATED, "*TIE").details["n_rotations_tied"] == 0


def test_a_dependent_without_rotations_is_tied_on_its_translations_only():
    """The solid top face has three dofs. Abaqus ignores a rotational tie dof on a node that
    has none, so skipping them is exact, not an approximation — and writing them would make
    ``bnbcd_str`` reject the deck."""
    fem = _tie_fem()
    records = tie_records(_constraint(fem), node_dofs(fem))
    assert {r.slave_dofs for r in records} == {(1, 2, 3)}


def test_a_node_on_both_surfaces_is_not_made_to_depend_on_itself():
    """Overlapping surfaces are ordinary in a real deck. A node listed on both sides is its
    own nearest neighbour at distance 0, and a BLDEP naming itself as its own master is a
    circular record Sestra cannot resolve."""
    fem = _tie_fem()
    shared = ada.Node((0.5, 0.5, 1.0), 20)  # no shell node is coincident with it
    fem.nodes.add(shared)
    fem.sets.get_nset_from_name("shell_face").add_members([shared])
    fem.sets.get_nset_from_name("solid_face").add_members([shared])

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert 20 not in {r.slave for r in records}
    assert sorted(r.slave for r in records) == [15, 16, 17, 18]
    assert _finding(report, conversion_report.APPROXIMATED, "*TIE").details["n_self_pairs"] == 1


def test_a_dependent_without_the_declared_dofs_is_not_called_outside_the_cutoff():
    """Two different problems with two different fixes. A node the tie declares rotations for
    that has none is *coincident* with its master — its geometry is perfect — and counting it
    as "outside the cutoff" sends the engineer to measure a gap that does not exist."""
    main = [ada.Node((0, 0, 1.0), 1), ada.Node((1, 0, 1.0), 2), ada.Node((1, 1, 1.0), 3), ada.Node((0, 1, 1.0), 4)]
    solid = _hex_nodes(0.0, 1.0, 11)  # its top face, ids 15-18, is coincident with the shell
    free = ada.Node((0, 0, 1.1), 30)  # attached to nothing, so six dofs
    fem = FEM(
        "mixed",
        nodes=Nodes(main + solid + [free]),
        elements=FemElements([Elem(1, main, ShellShapes.QUAD), Elem(2, solid, SolidShapes.HEX8)]),
    )
    m = Surface("main_surf", Surface.TYPES.NODE, fem.add_set(FemSet("m", main, "nset")), parent=fem)
    s_members = [fem.nodes.from_id(i) for i in (15, 16, 17, 18, 30)]
    s = Surface("sec_surf", Surface.TYPES.NODE, fem.add_set(FemSet("s", s_members, "nset")), parent=fem)
    fem.add_constraint(Constraint("T1", Constraint.TYPES.TIE, m, s, dofs=[4, 5, 6], parent=fem))

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert [r.slave for r in records] == [30], "only the six-dof dependent can carry a rotation tie"
    measure = _finding(report, conversion_report.APPROXIMATED, "*TIE").details
    assert measure["n_outside_cutoff"] == 0 and measure["n_without_declared_dofs"] == 4
    omitted = _finding(report, conversion_report.OMITTED, "*TIE")
    assert omitted.count == 4 and sorted(omitted.details["first_nodes"]) == [15, 16, 17, 18]
    assert "has none of the dofs the tie declares" in omitted.reason


def test_an_empty_tie_surface_is_an_omitted_finding():
    fem = _tie_fem()
    fem.sets.get_nset_from_name("shell_face")._members = []

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert records == []
    assert _finding(report, conversion_report.OMITTED, "*TIE").details["n_main_nodes"] == 0


# ── the companion BNBCD block ────────────────────────────────────────────────────


def test_the_tied_dofs_get_bnbcd_code_3_and_the_masters_stay_free():
    """BLDEP is only valid alongside these codes (manual printed 6-27), and they are derived
    from the records rather than restated, so the two blocks cannot disagree."""
    fem = _tie_fem()
    ndofs = node_dofs(fem)
    text = bnbcd_str([fem], tie_records(_constraint(fem), ndofs), None, ndofs)

    codes = {}
    for m in cards.re_bnbcd.finditer(text):
        d = m.groupdict()
        codes[int(float(d["nodeno"]))] = [int(float(c)) for c in d["content"].split()]

    for nid in (15, 16, 17, 18):
        assert codes[nid] == [3, 3, 3], "three dependent translations, and a 3-dof record"
    for nid in (1, 2, 3, 4):
        assert codes[nid] == [0] * 6, "the independent shell nodes stay free"


# ── the neighbouring path this must not disturb ──────────────────────────────────


def test_shell_to_solid_records_are_untouched_by_the_tie_path():
    """``shell2solid_records`` feeds this project's Sestra-proven deck. The tie shares its
    pairing kernel but not its rules — no cutoff, always the full arm — so the same fixture
    read as a shell-to-solid coupling must still give exactly the nine-term arms."""
    fem = _tie_fem()
    _constraint(fem)._con_type = Constraint.TYPES.SHELL2SOLID
    records = shell2solid_records(_constraint(fem))

    assert {r.slave: r.master for r in records} == {15: 1, 16: 2, 17: 3, 18: 4}
    for record in records:
        master, slave = fem.nodes.from_id(record.master), fem.nodes.from_id(record.slave)
        assert record.terms == tuple(LinDep(master.p, slave.p).to_integer_list())


# ── the facet interpolation ──────────────────────────────────────────────────────


def _element_main_tie_fem(
    secondary_points: dict[int, tuple[float, float, float]],
    *,
    main: Sequence[tuple[int, tuple[float, float, float]]] | None = None,
    shape=ShellShapes.QUAD,
    side="SPOS",
    pos_tol: float | None = None,
    metadata: dict | None = None,
    dofs=None,
) -> FEM:
    """One shell element as the **element** main surface, with free secondary nodes over it.

    The main nodes are given as ``(id, point)`` in the element's own winding order, and their
    ids are deliberately **not** in ascending order in most of the tests below: the facet
    contract is that ``SurfaceFacet.nodes`` comes back in Abaqus face order, and a weight
    attached to the wrong node of the facet is invisible if the ids happen to be sorted
    already. Task 1 lost a mutation to exactly that.

    ``side`` is what the surface entry names -- ``"SPOS"`` for the shell's own face (a QUAD /
    TRI6 / QUAD8 facet), ``"E1"`` for one of its edges (a LINE / LINE3 facet).

    The secondary nodes are attached to nothing, so ``node_dofs`` gives them six dofs and the
    rotations tie as well; that keeps the weight visible in two independent places in every
    record.
    """
    main = main or [(1, (0, 0, 0)), (2, (4, 0, 0)), (3, (4, 1, 0)), (4, (0, 1, 0))]
    main_nodes = [ada.Node(p, nid) for nid, p in main]
    secondary = [ada.Node(p, nid) for nid, p in secondary_points.items()]
    fem = FEM(
        "facet",
        nodes=Nodes(main_nodes + secondary),
        elements=FemElements([Elem(1, main_nodes, shape)]),
    )
    m = Surface(
        "main_surf",
        Surface.TYPES.ELEMENT,
        fem.add_set(FemSet("main_el", [fem.elements.from_id(1)], "elset")),
        el_face_index=side,
        parent=fem,
    )
    s = Surface("sec_surf", Surface.TYPES.NODE, fem.add_set(FemSet("sec", secondary, "nset")), parent=fem)
    fem.add_constraint(
        Constraint(
            "T1",
            Constraint.TYPES.TIE,
            m,
            s,
            dofs=dofs,
            pos_tol=pos_tol,
            metadata=dict(metadata or {}),
            parent=fem,
        )
    )
    return fem


#: The 1x4 quad the old node-distance rule could not cope with, with its corner ids out of
#: order so that a facet read in the wrong node order shows up.
_SKEW_QUAD = [(14, (0, 0, 0)), (11, (4, 0, 0)), (13, (4, 1, 0)), (12, (0, 1, 0))]


def _weight_of(record: BldepRecord, fem: FEM, slave: int) -> float:
    """The interpolation weight a record carries, read back out of its rotational terms.

    Rotations tie as ``(d, d, w)``, so the weight is readable straight off the record -- and
    then checked against the translational rigid arm, which must be the *same* weight times
    ``LinDep``. Reading it from one place and verifying the other is what makes these tests
    fail when a weight is applied to the arm but not the rotations, or vice versa.
    """
    rotational = [beta for s, m, beta in record.terms if s >= 4 and s == m]
    assert len(rotational) == 3 and len(set(rotational)) == 1, record.terms
    weight = rotational[0]
    master, slave_node = fem.nodes.from_id(record.master), fem.nodes.from_id(slave)
    arm = {(s, m): beta for s, m, beta in LinDep(master.p, slave_node.p).to_integer_list()}
    for s, m, beta in record.terms:
        if s <= 3:
            assert beta == pytest.approx(arm[(s, m)] * weight, abs=1e-14), (s, m, beta, weight)
    return weight


def test_a_secondary_node_on_a_main_facet_is_interpolated_over_all_of_its_nodes():
    """The change itself. A node lying on the main facet used to hang off whichever of the
    facet's nodes happened to be nearest, by a rigid arm up to half a facet long. It now hangs
    off *every* node of the facet with that facet's own shape-function weights, as one BLDEP
    record each for Sesam to sum -- which is the relation Abaqus applies.

    The point is deliberately off-centre, at ``xi = -0.5, eta = -0.4`` of the 1x4 quad, so the
    four bilinear weights are four *different* numbers: a test whose expected weights were all
    0.25 passes for any facet that is read symmetrically, including one read in the wrong node
    order."""
    fem = _element_main_tie_fem({20: (1.0, 0.3, 0.0)}, main=_SKEW_QUAD)

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert [r.master for r in records] == [14, 11, 13, 12], "one record per facet node, in facet order"
    assert {r.slave for r in records} == {20}
    weights = {r.master: _weight_of(r, fem, 20) for r in records}
    assert weights == {
        14: pytest.approx(0.525),
        11: pytest.approx(0.175),
        13: pytest.approx(0.075),
        12: pytest.approx(0.225),
    }
    assert sum(weights.values()) == pytest.approx(1.0)
    note = _finding(report, conversion_report.NOTE, "*TIE")
    assert note.details["n_tied"] == 1 and note.details["n_records"] == 4
    assert note.details["proj_max"] == pytest.approx(0.0), "the node is on the facet"


def test_the_interpolated_tie_transmits_a_rigid_body_motion_of_the_main_surface_exactly():
    """The property the whole construct rests on: the weights sum to 1, so a rigid body motion
    of the main surface reaches the secondary node as that same motion evaluated at the node.
    A constraint that fails this puts spurious stress into a model that is merely being moved.

    Checked on a **warped** QUAD8 facet with the secondary node off the surface, which is the
    hardest case there is: the four corners are not coplanar, the projection therefore lands on
    a triangulation of the patch rather than on the patch, and the arms run across a real gap.
    None of that matters to this invariant, which is the argument for the triangulation."""
    quad8 = [
        (31, (0.0, 0.0, 0.0)),
        (37, (2.0, 0.0, 0.0)),
        (33, (2.0, 2.0, 0.7)),  # out of the plane of the other three: the facet is warped
        (35, (0.0, 2.0, 0.0)),
        (32, (1.0, -0.2, 0.0)),  # mid-side nodes, off the mid-points so the facet is curved too
        (38, (2.2, 1.0, 0.3)),
        (34, (1.0, 2.1, 0.4)),
        (36, (-0.1, 1.0, 0.0)),
    ]
    fem = _element_main_tie_fem({50: (0.9, 1.1, 1.5)}, main=quad8, shape=ShellShapes.QUAD8, pos_tol=10.0)
    records = tie_records(_constraint(fem), node_dofs(fem))
    assert len(records) == 8, "every node of the facet carries a share"

    translation = np.array([0.31, -0.17, 0.44])
    rotation = np.array([0.013, -0.021, 0.007])

    def motion(node):
        return translation + np.cross(rotation, np.asarray(node.p, dtype=float))

    predicted = np.zeros(3)
    for record in records:
        master = fem.nodes.from_id(record.master)
        dof_value = {1: motion(master)[0], 2: motion(master)[1], 3: motion(master)[2]}
        dof_value.update({4: rotation[0], 5: rotation[1], 6: rotation[2]})
        for s_dof, m_dof, beta in record.terms:
            if s_dof <= 3:
                predicted[s_dof - 1] += beta * dof_value[m_dof]

    assert predicted == pytest.approx(motion(fem.nodes.from_id(50)), abs=1e-12)


def test_a_facet_node_whose_weight_is_zero_gets_no_record():
    """A shape function is *exactly* zero at a good many points of its own facet: at the
    mid-side node of a TRI6 edge, five of the six vanish. Writing those anyway means five BLDEP
    records of nothing but 0.0 coefficients, each of which still declares the master's dofs to
    be read and so has to be held free in BNBCD for no reason."""
    tri6 = [
        (21, (0.0, 0.0, 0.0)),
        (26, (3.0, 0.0, 0.0)),
        (23, (0.0, 3.0, 0.0)),
        (25, (1.5, 0.0, 0.0)),  # mid-side of edge 0-1
        (22, (1.5, 1.5, 0.0)),  # mid-side of edge 1-2
        (24, (0.0, 1.5, 0.0)),  # mid-side of edge 2-0
    ]
    fem = _element_main_tie_fem({40: (1.5, 0.0, 0.0)}, main=tri6, shape=ShellShapes.TRI6)
    records = tie_records(_constraint(fem), node_dofs(fem))

    assert [r.master for r in records] == [25], "only the mid-side node the point sits on"
    assert _weight_of(records[0], fem, 40) == pytest.approx(1.0)


def test_the_quadratic_corner_weights_go_negative_at_the_centre_of_a_tri6():
    """A TRI6 corner function is -1/9 at the centroid and the mid-side ones are 4/9. Negative
    coefficients are perfectly good BLDEP, and a writer that clamped or dropped them would
    break the partition of unity the constraint depends on -- so they are pinned."""
    tri6 = [
        (21, (0.0, 0.0, 0.0)),
        (26, (3.0, 0.0, 0.0)),
        (23, (0.0, 3.0, 0.0)),
        (25, (1.5, 0.0, 0.0)),
        (22, (1.5, 1.5, 0.0)),
        (24, (0.0, 1.5, 0.0)),
    ]
    fem = _element_main_tie_fem({40: (1.0, 1.0, 0.0)}, main=tri6, shape=ShellShapes.TRI6)
    records = tie_records(_constraint(fem), node_dofs(fem))

    weights = {r.master: _weight_of(r, fem, 40) for r in records}
    assert weights == {
        21: pytest.approx(-1 / 9),
        26: pytest.approx(-1 / 9),
        23: pytest.approx(-1 / 9),
        25: pytest.approx(4 / 9),
        22: pytest.approx(4 / 9),
        24: pytest.approx(4 / 9),
    }
    assert sum(weights.values()) == pytest.approx(1.0)


def test_a_shell_edge_facet_interpolates_along_the_edge():
    """A main surface that names shell *edges* (``E1``) is a set of one-dimensional facets, and
    it is what the acceptance deck's tie is mostly made of -- 675 of its 807 facets. A node over
    the quarter point of the edge is interpolated 3/4 : 1/4, not snapped to the nearer end."""
    fem = _element_main_tie_fem({20: (1.0, 0.0, 0.5)}, main=_SKEW_QUAD, side="E1", pos_tol=1.0)

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert [r.master for r in records] == [14, 11], "the two ends of edge E1, in edge order"
    assert [_weight_of(r, fem, 20) for r in records] == [pytest.approx(0.75), pytest.approx(0.25)]
    note = _finding(report, conversion_report.NOTE, "*TIE")
    assert note.details["main_facet_shapes"] == {"LineShapes.LINE": 1}
    assert note.details["proj_max"] == pytest.approx(0.5), "the node is half a unit off the edge"


# ── the cutoff, now a projection distance ────────────────────────────────────────


def test_a_node_lying_on_a_stretched_main_facet_is_tied_however_far_its_nodes_are():
    """The defect the old rule kept having to widen for. On a 1x4 facet -- the shape of any
    stiffener web -- a secondary node sitting *exactly on the main surface* is 2.06 from the
    nearest main node, which no node-distance cutoff can distinguish from a 2.06 gap. Measured
    to the surface it is at zero, and the question does not arise."""
    fem = _element_main_tie_fem({10: (2.0, 0.5, 0.0), 11: (1.0, 0.5, 0.0)})

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert sorted({r.slave for r in records}) == [10, 11], "both are on the main surface"
    assert [f.one_line() for f in report.of_kind(conversion_report.OMITTED)] == []
    note = _finding(report, conversion_report.NOTE, "*TIE")
    assert note.details["proj_max"] == pytest.approx(0.0)
    assert note.details["cutoff_rule"] == "5% of the largest main-facet half-diagonal"
    assert note.details["cutoff"] == pytest.approx(0.05 * np.sqrt(17) / 2)


def test_a_node_off_the_main_surface_is_still_omitted_and_named():
    """The projection is not "tie everything": a node a facet away from the surface is omitted
    and named, which is what makes the finding worth reading."""
    fem = _element_main_tie_fem({10: (2.0, 0.5, 0.0), 11: (2.0, 0.5, 9.0)})

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert {r.slave for r in records} == {10}
    omitted = _finding(report, conversion_report.OMITTED, "*TIE")
    assert omitted.details["first_nodes"] == [11]
    assert "farther from the main surface" in omitted.reason


def test_a_declared_position_tolerance_is_a_distance_to_the_surface_not_a_slack():
    """``position tolerance`` is Abaqus' distance from the secondary node to the main
    *surface*, and now that the projection exists that is what it is compared against. The old
    rule added the largest facet half-diagonal to it -- 2.06 on this facet -- so a node 0.5 off
    the surface was tied even when the deck declared a tolerance of 0.1. It is not any more."""
    on_gap = {10: (2.0, 0.5, 0.5)}
    assert len(tie_records(_constraint(_element_main_tie_fem(on_gap, pos_tol=1.0)), None)) == 4, "0.5 <= 1.0"

    with conversion_report.collect() as report:
        assert tie_records(_constraint(_element_main_tie_fem(on_gap, pos_tol=0.1)), None) == []

    omitted = _finding(report, conversion_report.OMITTED, "*TIE")
    assert omitted.details["cutoff"] == pytest.approx(0.1)
    assert omitted.details["cutoff_rule"] == "declared position tolerance"


def test_the_facet_size_is_measured_over_the_facets_the_surface_actually_covers():
    """The cutoff scale and the master node set now come from one reading of the surface.

    They did not. The old measurement resolved the sides itself and answered "the whole
    element" for an entry that names no face identifier, while ``surface_nodes`` reads such an
    entry as the element's *free faces*. On a hexahedron the difference is visible: half the
    body diagonal is sqrt(3)/2, half the longest diagonal of any one face is sqrt(2)/2."""
    corners = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(corners)]
    fem = FEM("hex", nodes=Nodes(nodes), elements=FemElements([Elem(1, nodes, SolidShapes.HEX8)]))
    fset = fem.add_set(FemSet("_blank", [fem.elements.from_id(1)], "elset"))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="", parent=fem)

    facets = surface_facets(surf)
    assert len(facets) == 6, "no neighbour, so every face of the hex is free"
    assert _max_facet_half_diagonal(facets) == pytest.approx(np.sqrt(2) / 2)
    assert _max_facet_half_diagonal(facets) < np.sqrt(3) / 2, "not half the body diagonal"


# ── determinism ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("low_is_near", [False, True])
def test_two_equidistant_facets_are_broken_on_the_lowest_node_ids(reverse, low_is_near):
    """313 exact *node* distance ties occur in one real deck, and a facet tie is commoner
    still: every node on the shared edge of two faces is equidistant from both. Which facet
    wins must be a property of the mesh, not of the order the region resolver happened to hand
    the elements back in -- a deck that reshuffles its BLDEP pairing between two conversions of
    the same model cannot be diffed.

    Two parallel quads with a secondary node exactly between them. ``low_is_near`` swaps which
    plate carries the low ids, so the test would also fail for a writer that simply always
    picked the first or the geometrically-lower facet."""
    low = [1, 2, 3, 4]
    high = [11, 12, 13, 14]
    corners = [(0, 0), (2, 0), (2, 2), (0, 2)]
    near_ids, far_ids = (low, high) if low_is_near else (high, low)
    near = [ada.Node((x, y, 0.0), nid) for nid, (x, y) in zip(near_ids, corners)]
    far = [ada.Node((x, y, 4.0), nid) for nid, (x, y) in zip(far_ids, corners)]
    slave = ada.Node((1.0, 1.0, 2.0), 99)

    fem = FEM(
        "pair",
        nodes=Nodes(near + far + [slave]),
        elements=FemElements([Elem(1, near, ShellShapes.QUAD), Elem(2, far, ShellShapes.QUAD)]),
    )
    els = [fem.elements.from_id(i) for i in (1, 2)]
    m = Surface(
        "main_surf",
        Surface.TYPES.ELEMENT,
        fem.add_set(FemSet("main_el", list(reversed(els)) if reverse else els, "elset")),
        el_face_index="SPOS",
        parent=fem,
    )
    s = Surface("sec_surf", Surface.TYPES.NODE, fem.add_set(FemSet("sec", [slave], "nset")), parent=fem)
    fem.add_constraint(Constraint("T1", Constraint.TYPES.TIE, m, s, pos_tol=3.0, parent=fem))

    records = tie_records(_constraint(fem), node_dofs(fem))
    assert sorted(r.master for r in records) == low, "the facet with the lowest node ids wins"


# ── what the finding now says ────────────────────────────────────────────────────


def test_an_interpolated_tie_is_a_note_and_not_an_approximation():
    """The nearest-node arm was an approximation and was reported as one, carrying the arm
    length distribution that sized it. There is no arm to size any more: a node that projects
    onto a facet within the tolerance is tied to that facet the way Abaqus ties it. Calling
    that an approximation would leave ``ada convert`` reporting one for every tie it writes
    correctly, which is how a report stops being read."""
    fem = _element_main_tie_fem({10: (2.0, 0.5, 0.0)})

    with conversion_report.collect() as report:
        tie_records(_constraint(fem), node_dofs(fem))

    assert [f.one_line() for f in report.of_kind(conversion_report.APPROXIMATED)] == []
    note = _finding(report, conversion_report.NOTE, "*TIE")
    assert "interpolated over the main facet it projects onto" in note.reason
    assert "dist_max" not in note.details, "the measure is a distance to the surface now"
    assert set(note.details) >= {"proj_max", "proj_p95", "proj_mean", "n_records", "records_per_tied_node"}


def test_each_interpolated_tie_keeps_its_own_measure():
    """Findings deduplicate on (kind, stage, keyword, reason) and merge details first-wins, so
    a reason shared by every tie would collapse them and report the first one's numbers for the
    second one's nodes."""
    near = _element_main_tie_fem({10: (2.0, 0.5, 0.05)}, pos_tol=1.0)
    far = _element_main_tie_fem({10: (2.0, 0.5, 0.5)}, pos_tol=1.0)
    _constraint(far)._name = "T2"

    with conversion_report.collect() as report:
        tie_records(_constraint(near), node_dofs(near))
        tie_records(_constraint(far), node_dofs(far))

    notes = [f for f in report.of_kind(conversion_report.NOTE) if f.keyword == "*TIE"]
    assert len(notes) == 2, [f.one_line() for f in notes]
    assert sorted(f.details["proj_max"] for f in notes) == pytest.approx([0.05, 0.5])


def test_a_node_that_is_itself_a_node_of_its_facet_is_not_made_to_depend_on_itself():
    """A secondary node listed on the main surface as well is its own master at weight 1. The
    nearest-node form caught that as an exact self-pair; the facet form has to catch it as
    membership of the facet, because the weight on it need not be the whole of the record."""
    fem = _element_main_tie_fem({10: (2.0, 0.5, 0.0)}, main=_SKEW_QUAD)
    shared = fem.nodes.from_id(11)
    fem.sets.get_nset_from_name("sec").add_members([shared])

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert 11 not in {r.slave for r in records}
    assert _finding(report, conversion_report.NOTE, "*TIE").details["n_self_pairs"] == 1


def test_an_independent_node_that_is_dependent_too_is_reported_as_a_suspect():
    """A facet interpolation names up to eight independent nodes where the nearest-node form
    named one, so a linear dependency whose master is itself linearly dependent becomes eight
    times likelier. Sesam is not promised to resolve that, and if it does the kinematics are
    not the model's -- the master's own motion is no longer free.

    Built from the tie-break: node 11 lies *on* the big low-id facet while being a *node* of
    the small high-id one, so it is not a self-pair on the facet it projects onto, and node 50
    -- which projects onto the small facet -- takes it as a master."""
    plate = [ada.Node(p, i + 1) for i, p in enumerate([(0, 0, 0), (4, 0, 0), (4, 1, 0), (0, 1, 0)])]
    fin = [
        ada.Node((2.0, 0.5, 0.0), 11),  # on the plate, and a corner of the fin
        ada.Node((2.0, 0.5, 2.0), 12),
        ada.Node((3.0, 0.5, 2.0), 13),
        ada.Node((3.0, 0.5, 0.0), 14),  # on the plate too
    ]
    slave = ada.Node((2.5, 0.5, 1.0), 50)  # inside the fin
    fem = FEM(
        "chain",
        nodes=Nodes(plate + fin + [slave]),
        elements=FemElements([Elem(1, plate, ShellShapes.QUAD), Elem(2, fin, ShellShapes.QUAD)]),
    )
    m = Surface(
        "main_surf",
        Surface.TYPES.ELEMENT,
        fem.add_set(FemSet("main_el", [fem.elements.from_id(1), fem.elements.from_id(2)], "elset")),
        el_face_index="SPOS",
        parent=fem,
    )
    s = Surface("sec_surf", Surface.TYPES.NODE, fem.add_set(FemSet("sec", [slave, fin[0]], "nset")), parent=fem)
    fem.add_constraint(Constraint("T1", Constraint.TYPES.TIE, m, s, parent=fem))

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert 11 in {r.slave for r in records}, "node 11 projects onto the plate, which is not its own facet"
    assert 11 in {r.master for r in records}, "and carries node 50, which projects onto the fin"
    suspect = _finding(report, conversion_report.SUSPECT, "BLDEP")
    assert suspect.count == 1 and suspect.details["first_nodes"] == [11]


# ── the nearest-node fallback ────────────────────────────────────────────────────


def test_a_main_surface_with_no_facets_falls_back_to_the_nearest_node_and_says_so():
    """A ``NODE``-type surface, or a plain node set, has no geometry to project onto. The
    nearest-node arm is then the only thing available and is exactly what it was before -- but
    it is an approximation, so it is reported as one rather than left to look like the facet
    path."""
    fem = _tie_fem()

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert sorted(r.slave for r in records) == [15, 16, 17, 18]
    assert [f.one_line() for f in report.of_kind(conversion_report.NOTE)] == []
    finding = _finding(report, conversion_report.APPROXIMATED, "*TIE")
    assert "names no element facet to project onto" in finding.reason
    assert finding.details["cutoff_rule"] == "max nearest-neighbour node spacing"
    assert finding.details["main_region"] == "Surface"


def test_a_facet_topology_with_no_shape_functions_falls_back_and_names_it():
    """TRI7 / QUAD9 carry a centre node, so they are a different interpolation; Abaqus has no
    such element and this writer has no shape functions for them. Quietly using the TRI6 ones
    over the first six nodes would be an unreported approximation of a facet the report says was
    handled exactly, so the tie falls back and the shape is named."""
    tri7 = [
        (21, (0.0, 0.0, 0.0)),
        (26, (3.0, 0.0, 0.0)),
        (23, (0.0, 3.0, 0.0)),
        (25, (1.5, 0.0, 0.0)),
        (22, (1.5, 1.5, 0.0)),
        (24, (0.0, 1.5, 0.0)),
        (27, (1.0, 1.0, 0.0)),
    ]
    fem = _element_main_tie_fem({40: (1.0, 1.0, 0.0)}, main=tri7, shape=ShellShapes.TRI7)

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert [r.master for r in records] == [27], "the nearest main node, not an interpolation"
    finding = _finding(report, conversion_report.APPROXIMATED, "*TIE")
    assert "no facet of the main surface has a topology this writer can interpolate over" in finding.reason
    assert finding.details["main_facet_shapes"] == {"ShellShapes.TRI7": 1}


def test_a_warped_quad_facet_projects_the_same_whichever_corner_it_is_numbered_from():
    """A quadrilateral facet is bilinear and in general **not planar**, so there is no plane to
    drop a perpendicular onto. The projection splits it into corner triangles instead -- and
    along *both* diagonals, not one, because one diagonal makes the answer depend on which
    corner the element happens to be numbered from. The same physical facet, listed starting
    from each of its four corners in turn, must give the same distance and the same weight on
    each node.

    The node is over the middle of a facet warped 1.2 out of plane, where the two triangulations
    disagree by half a unit -- enough to put it outside the position tolerance under one
    numbering and on the surface under another."""
    corners = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 1.2), (0.0, 2.0, 0.0)]
    ids = [17, 13, 19, 15]

    answers = []
    for start in range(4):
        order = [(ids[(start + k) % 4], corners[(start + k) % 4]) for k in range(4)]
        fem = _element_main_tie_fem({70: (1.0, 1.0, 0.0)}, main=order)
        with conversion_report.collect() as report:
            records = tie_records(_constraint(fem), node_dofs(fem))
        assert records, f"numbering from corner {start} left the node untied"
        answers.append(
            (
                _finding(report, conversion_report.NOTE, "*TIE").details["proj_max"],
                {r.master: round(_weight_of(r, fem, 70), 12) for r in records},
            )
        )

    assert answers[0][0] == pytest.approx(0.0), "the node is on the warped patch"
    for start, (distance, weights) in enumerate(answers[1:], start=1):
        assert distance == pytest.approx(answers[0][0]), f"numbering from corner {start} moved the surface"
        assert weights == answers[0][1], f"numbering from corner {start} moved the weights"


def test_a_quad8_mid_side_node_is_the_only_master_for_a_point_sitting_on_it():
    """QUAD8's four mid-side functions are 1 at their own node and 0 at every other, and they
    are *interleaved* with nothing -- node 5 is on edge 0-1, node 6 on edge 1-2. A writer that
    emitted them in the wrong order still sums to 1 and still passes the rigid-body test, so
    the ordering is pinned on its own, once per mid-side node."""
    quad8 = [
        (31, (0.0, 0.0, 0.0)),
        (37, (2.0, 0.0, 0.0)),
        (33, (2.0, 2.0, 0.0)),
        (35, (0.0, 2.0, 0.0)),
        (32, (1.0, 0.0, 0.0)),  # edge 0-1
        (38, (2.0, 1.0, 0.0)),  # edge 1-2
        (34, (1.0, 2.0, 0.0)),  # edge 2-3
        (36, (0.0, 1.0, 0.0)),  # edge 3-0
    ]
    for on, expected in ((32, (1.0, 0.0, 0.0)), (38, (2.0, 1.0, 0.0)), (34, (1.0, 2.0, 0.0)), (36, (0.0, 1.0, 0.0))):
        fem = _element_main_tie_fem({60: expected}, main=quad8, shape=ShellShapes.QUAD8)
        records = tie_records(_constraint(fem), node_dofs(fem))
        assert [r.master for r in records] == [on], f"at {expected} only node {on} should carry the tie"
        assert _weight_of(records[0], fem, 60) == pytest.approx(1.0)


def test_a_node_past_the_end_of_a_shell_edge_is_clamped_to_that_end():
    """The projection is onto the facet, not onto the infinite line through it. Without the
    clamp a node off the end of the edge gets a parametric coordinate outside the reference
    domain, which extrapolates: a LINE2 weight goes negative at one end and above 1 at the
    other, so the node is held by a lever rather than tied to the edge."""
    fem = _element_main_tie_fem({20: (-3.0, 0.0, 0.0)}, main=_SKEW_QUAD, side="E1", pos_tol=5.0)
    records = tie_records(_constraint(fem), node_dofs(fem))

    assert [r.master for r in records] == [14], "the end of the edge, on its own"
    assert _weight_of(records[0], fem, 20) == pytest.approx(1.0)


def test_a_main_surface_that_is_only_partly_projectable_says_which_part_was_left_out():
    """A surface can be a mixture. Dropping the facets with no shape functions and carrying on
    with the rest is the right thing to do -- the alternative is to lose the whole tie over one
    odd element -- but it narrows the surface the secondary nodes are measured against, so a
    node over the dropped part lands on a farther facet or on none. That has to be said."""
    quad = [ada.Node(p, i) for i, p in ((1, (0, 0, 0)), (2, (2, 0, 0)), (3, (2, 2, 0)), (4, (0, 2, 0)))]
    tri7 = [
        ada.Node((4.0, 0.0, 0.0), 21),
        ada.Node((6.0, 0.0, 0.0), 22),
        ada.Node((4.0, 2.0, 0.0), 23),
        ada.Node((5.0, 0.0, 0.0), 24),
        ada.Node((5.0, 1.0, 0.0), 25),
        ada.Node((4.0, 1.0, 0.0), 26),
        ada.Node((4.6, 0.6, 0.0), 27),
    ]
    slave = ada.Node((1.0, 1.0, 0.0), 70)
    fem = FEM(
        "mixed",
        nodes=Nodes(quad + tri7 + [slave]),
        elements=FemElements([Elem(1, quad, ShellShapes.QUAD), Elem(2, tri7, ShellShapes.TRI7)]),
    )
    m = Surface(
        "main_surf",
        Surface.TYPES.ELEMENT,
        fem.add_set(FemSet("main_el", [fem.elements.from_id(1), fem.elements.from_id(2)], "elset")),
        el_face_index="SPOS",
        parent=fem,
    )
    s = Surface("sec_surf", Surface.TYPES.NODE, fem.add_set(FemSet("sec", [slave], "nset")), parent=fem)
    fem.add_constraint(Constraint("T1", Constraint.TYPES.TIE, m, s, parent=fem))

    with conversion_report.collect() as report:
        records = tie_records(_constraint(fem), node_dofs(fem))

    assert sorted(r.master for r in records) == [1, 2, 3, 4], "the quad still interpolates"
    finding = _finding(report, conversion_report.APPROXIMATED, "*TIE")
    assert finding.count == 1
    assert finding.details["skipped_facet_shapes"] == {"ShellShapes.TRI7": 1}
    assert finding.details["n_main_facets"] == 2
    note = _finding(report, conversion_report.NOTE, "*TIE")
    assert note.details["main_facet_shapes"] == {"ShellShapes.QUAD": 1}


def test_a_weight_of_one_leaves_the_arm_exactly_as_the_unweighted_builder_writes_it():
    """Two things, because the second is what the first is for. A weight scales *every*
    coefficient of the record, translational arm and rotational identity alike -- a writer that
    scaled one and not the other would still sum to 1 over the facet's translations and still
    pass the rigid-body check. And the default weight leaves the terms bit for bit what the
    unweighted builder writes, which is what every other constraint type -- including this
    project's Sestra-proven coupling -- goes on receiving."""
    master, slave = ada.Node((1.0, 2.0, 3.0), 1), ada.Node((4.0, 6.0, 8.5), 2)
    plain = _rigid_terms(master, slave, ALL_DOFS, 6, 6)
    weighted = _rigid_terms(master, slave, ALL_DOFS, 6, 6, 1.0)
    assert plain == weighted
    assert [beta for _, _, beta in _rigid_terms(master, slave, ALL_DOFS, 6, 6, 0.25)] == [
        pytest.approx(beta * 0.25) for _, _, beta in plain
    ]


def test_the_interpolated_records_survive_the_pair_merge_and_bnbcd():
    """Several records naming the same dependent node is the mechanism -- Sesam sums them --
    but only one record may name a given (SLAVE, MASTER) *pair*, and the companion BNBCD block
    is derived from whatever comes out. Both are checked on the real path, ``bldep_records``,
    rather than on ``tie_records`` alone."""
    fem = _element_main_tie_fem({20: (1.0, 0.3, 0.0)}, main=_SKEW_QUAD)
    ndofs = node_dofs(fem)
    records = bldep_records(fem, ndofs)

    assert len({(r.slave, r.master) for r in records}) == len(records) == 4
    text = bnbcd_str([fem], records, None, ndofs)  # must not raise
    codes = {}
    for m in cards.re_bnbcd.finditer(text):
        d = m.groupdict()
        codes[int(float(d["nodeno"]))] = [int(float(c)) for c in d["content"].split()]
    assert codes[20] == [3] * 6, "the dependent node's six dofs, all linearly dependent"
    for nid in (11, 12, 13, 14):
        assert codes[nid] == [0] * 6, "every node of the facet stays free"

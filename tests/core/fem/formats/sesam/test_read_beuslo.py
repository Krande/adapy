"""The Sesam reader and BEUSLO -- a surface pressure on the way in.

``write_loads.load_pressure`` learned to write one BEUSLO per shell element and side, verified
against Sestra V11.3-00 on a simply supported strip. Nothing read it, so a pressure-loaded model
round-tripped through a Sesam deck came back *unloaded* -- the failure mode the writer's own
commit calls out, because two such runs agree with each other perfectly.

The one field these tests are really about is the **sign**. Measured on one shell with its nodes
counter-clockwise in x-y (normal +z): Abaqus puts a positive ``*Dsload P`` into the face it
names, while a positive BEUSLO intensity pushes along the element's *negative* normal for every
legal SIDE, because Sestra computes the load in the mid-plane and SIDE therefore cannot carry a
direction. So the writer puts ``+q`` on the positive face and ``-q`` with SIDE 2 on the negative
one, and this reads that back: a ``Load`` written positive on ``SPOS`` comes back positive on
``SPOS``, and one written positive on ``SNEG`` comes back positive on ``SNEG``.

Hand-written minimal decks, one field per test.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import Elem, FemSet
from ada.fem.containers import FemElements
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.read.read_loads import READ_STEP_NAME, STAGE, get_loads
from ada.fem.formats.sesam.write.write_loads import (
    PRESSURE_INTNO,
    PRESSURE_LAYER,
    PRESSURE_LOTYP,
    SIDE_NEGATIVE,
    SIDE_POSITIVE,
)
from ada.fem.formats.sesam.write.write_utils import write_ff
from ada.fem.loads import Load
from ada.fem.shapes.definitions import ShellShapes

Q = 1000.0


def _quad_fem(n_elements: int = 2, named_sets: dict[str, list[int]] | None = None) -> ada.FEM:
    """``n_elements`` 4-noded shells, element ids counting from 1, plus any named element sets."""
    nodes, elements, nid = [], [], 1
    for i in range(n_elements):
        el_nodes = [ada.Node((i + k * 0.1, k * 0.2, 0.0), nid + k) for k in range(4)]
        nodes += el_nodes
        nid += 4
        elements.append(Elem(i + 1, el_nodes, ShellShapes.QUAD))
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    fem.elements = FemElements(elements, fem_obj=fem)
    for el in elements:
        el.parent = fem
    for name, ids in (named_sets or {}).items():
        fem.sets.add(FemSet(name, [fem.elements.from_id(i) for i in ids], "elset", parent=fem))
    return fem


def _beuslo(
    elno: int,
    intensities,
    llc: int = 1,
    side: int = SIDE_POSITIVE,
    lotyp: int = PRESSURE_LOTYP,
    complx: int = 0,
    layer: int = PRESSURE_LAYER,
    intno: int = PRESSURE_INTNO,
    ndof: int | None = None,
) -> str:
    """One BEUSLO record, written the way ``write_loads.load_pressure`` writes it."""
    intensities = list(intensities)
    return write_ff(
        "BEUSLO",
        [
            (llc, lotyp, complx, layer),
            (elno, len(intensities) if ndof is None else ndof, intno, side),
            tuple(intensities),
        ],
    )


def _tdload(llc: int, name: str) -> str:
    return write_ff("TDLOAD", [(4, llc, 100 + len(name), 0), (name,)])


def _read(bulk: str, fem: ada.FEM):
    """``(step, findings)`` -- the step ``get_loads`` builds, and what it reported about BEUSLO."""
    step, findings = _read_all(bulk, fem)
    return step, [f for f in findings if f.keyword == "BEUSLO"]


def _read_all(bulk: str, fem: ada.FEM):
    """``(step, every finding of the reader)``."""
    with conversion_report.collect() as rep:
        step = get_loads(bulk, fem)
    return step, [f for f in rep.findings if f.stage == STAGE]


def _loads(step) -> list:
    return list(step.loads)


# --- the record ------------------------------------------------------------------------------


def test_one_record_is_a_pressure_load_on_the_decks_own_element_set():
    fem = _quad_fem(1, {"TOP": [1]})
    step, findings = _read(_beuslo(1, [Q] * 4), fem)

    assert not findings
    # A Sesam FEM file holds no analysis step -- Sestra's control data is a separate
    # sestra.inp -- so the step is the reader's own container for the deck's load cases.
    assert (step.name, step.type) == (READ_STEP_NAME, step.TYPES.STATIC)
    assert step is fem.steps[0]
    (load,) = _loads(step)
    assert load.type == Load.TYPES.PRESSURE
    assert load.magnitude == Q
    # The set is the deck's own, not a generated one: the writer wrote the surface's set as
    # GSETMEMB and ``get_sets`` read it back before the loads.
    assert load.surface.fem_set.name == "TOP"
    assert [m.id for m in load.surface.fem_set.members] == [1]
    assert load.surface.el_face_index == 1, "SIDE 1 is the face on the element's positive normal"


def test_side_2_is_the_negative_face_and_the_sign_comes_back_positive():
    """The whole sign convention in one test: the writer wrote ``-q`` and SIDE 2 for a pressure
    the model gave as ``+q`` on ``SNEG``, so the reader has to undo exactly that."""
    fem = _quad_fem(1, {"BTM": [1]})
    step, findings = _read(_beuslo(1, [-Q] * 4, side=SIDE_NEGATIVE), fem)

    assert not findings
    (load,) = _loads(step)
    assert load.magnitude == Q, "positive on SNEG, as it was written"
    assert load.surface.el_face_index == -1


def test_a_negative_pressure_on_the_positive_face_stays_negative():
    """Sign and face are two statements, not one: SIDE 1 with a negative intensity is suction on
    the positive face, and reading the sign as the face would turn it into pressure on SNEG."""
    fem = _quad_fem(1, {"TOP": [1]})
    step, _ = _read(_beuslo(1, [-Q] * 4, side=SIDE_POSITIVE), fem)

    (load,) = _loads(step)
    assert (load.magnitude, load.surface.el_face_index) == (-Q, 1)


def test_records_sharing_a_case_face_and_magnitude_become_one_load():
    """A deck writes one record per element; a ``Load`` covers a set. So they group back."""
    fem = _quad_fem(3, {"SHELLS": [1, 2, 3]})
    step, _ = _read("".join(_beuslo(i, [Q] * 4) for i in (1, 2, 3)), fem)

    (load,) = _loads(step)
    assert load.surface.fem_set.name == "SHELLS"
    assert sorted(m.id for m in load.surface.fem_set.members) == [1, 2, 3]


def test_two_magnitudes_in_one_case_are_two_loads():
    fem = _quad_fem(2)
    step, _ = _read(_beuslo(1, [Q] * 4) + _beuslo(2, [2 * Q] * 4), fem)

    loads = _loads(step)
    assert [ld.magnitude for ld in loads] == [Q, 2 * Q], "in the order their records appear"
    assert [[m.id for m in ld.surface.fem_set.members] for ld in loads] == [[1], [2]]


def test_both_faces_in_one_case_are_two_loads():
    fem = _quad_fem(2)
    step, _ = _read(_beuslo(1, [Q] * 4) + _beuslo(2, [-Q] * 4, side=SIDE_NEGATIVE), fem)

    loads = _loads(step)
    assert [ld.magnitude for ld in loads] == [Q, Q]
    assert [ld.surface.el_face_index for ld in loads] == [1, -1]


def test_a_group_no_element_set_matches_gets_one_of_its_own():
    """Two of three shells loaded, and no set holds exactly those two."""
    fem = _quad_fem(3, {"SHELLS": [1, 2, 3]})
    step, _ = _read(_beuslo(1, [Q] * 4) + _beuslo(3, [Q] * 4), fem)

    (load,) = _loads(step)
    assert load.surface.fem_set.name not in ("SHELLS",)
    assert sorted(m.id for m in load.surface.fem_set.members) == [1, 3]
    assert load.surface.fem_set in fem.sets.sets, "and it is a set of the model, not a loose one"


# --- the load cases ---------------------------------------------------------------------------


def test_the_load_cases_are_the_ones_tdload_names():
    fem = _quad_fem(2)
    bulk = _tdload(1, "GRAV_CASE") + _tdload(2, "WIND") + _beuslo(1, [Q] * 4, llc=1) + _beuslo(2, [2 * Q] * 4, llc=2)
    step, _ = _read(bulk, fem)

    assert list(step.load_cases) == ["GRAV_CASE", "WIND"]
    assert [[ld.magnitude for ld in lc.loads] for lc in step.load_cases.values()] == [[Q], [2 * Q]]


def test_an_unnamed_load_case_is_lc_plus_its_number():
    fem = _quad_fem(1)
    step, _ = _read(_beuslo(1, [Q] * 4, llc=3), fem)

    assert list(step.load_cases) == ["LC3"]


def test_a_deck_with_no_beuslo_gains_no_step():
    """Every Sesam deck read before this change has none, and a step nobody asked for would show
    up in every conversion out of Sesam."""
    fem = _quad_fem(1)
    step, findings = _read("GNODE     1.0\n", fem)

    assert step is None
    assert len(fem.steps) == 0
    assert not findings


# --- refusals, by name -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, field, value",
    [
        ({"lotyp": 2}, "lotyp", 2),
        ({"lotyp": 3}, "lotyp", 3),
        ({"complx": 1}, "complx", 1),
        ({"layer": 1}, "layer", 1),
        ({"intno": 2}, "intno", 2),
    ],
)
def test_the_fields_ada_cannot_hold_are_refused_by_name(kwargs, field, value):
    """Each with the reason it is refused rather than read as a plain pressure. LOTYP 2 is a
    three-component vector load and LOTYP 3 no load type at all; COMPLX 1 carries a phase; and a
    nonzero LAYER or INTNO is *ignored* by Sestra, so the deck does not mean what it says.
    """
    fem = _quad_fem(1)
    step, findings = _read(_beuslo(1, [Q] * 4, **kwargs), fem)

    assert step is None, "a refused record leaves no load, and no step either"
    assert len(findings) == 1
    assert findings[0].kind == conversion_report.OMITTED
    assert findings[0].details == {field: value}
    assert field.upper() in findings[0].reason


@pytest.mark.parametrize("side", [0, 3, 4])
def test_a_side_that_names_no_shell_face_is_refused_by_name(side):
    """SIDE 1 and 2 are the two faces. Sestra also takes 3 -- which it computes in the mid-plane,
    so it names no face for a ``Surface`` to carry -- and refuses 0 and 4 and up outright
    ("Illegal side index specified on BEUSLO record")."""
    fem = _quad_fem(1)
    step, findings = _read(_beuslo(1, [Q] * 4, side=side), fem)

    assert step is None
    assert len(findings) == 1 and findings[0].details == {"side": side}
    assert "SIDE" in findings[0].reason


def test_an_element_the_deck_does_not_have_is_refused_by_name():
    fem = _quad_fem(1)
    step, findings = _read(_beuslo(9, [Q] * 4), fem)

    assert step is None
    assert len(findings) == 1 and "not in the deck" in findings[0].reason


def test_an_intensity_count_that_is_not_the_node_count_is_refused_by_name():
    """Sestra's own check: "Load intensity vector size does not match dof count for load". One
    intensity per node is what the record means, so anything else is a deck that disagrees with
    itself and there is no telling which half is right."""
    fem = _quad_fem(1)
    step, findings = _read(_beuslo(1, [Q, Q], ndof=2), fem)

    assert step is None
    assert len(findings) == 1
    assert findings[0].details == {"ndof": 2, "n_intensities": 2, "n_nodes": 4}


# --- what is approximated rather than refused --------------------------------------------------


def test_a_non_uniform_pressure_is_approximated_with_its_values():
    """One ``Load`` carries one magnitude, and BEUSLO carries one intensity per node. Their mean
    is read, which keeps the resultant of a bilinear quad exactly -- the consistent load vector
    is ``q_i A / 4`` per node, so the total is ``A`` times the mean -- and the values themselves
    go in the report, because an approximation nobody can size is a guess."""
    fem = _quad_fem(1)
    intensities = [1000.0, 1000.0, 2000.0, 2000.0]
    step, findings = _read(_beuslo(1, intensities), fem)

    (load,) = _loads(step)
    assert load.magnitude == 1500.0
    assert len(findings) == 1
    assert findings[0].kind == conversion_report.APPROXIMATED
    assert findings[0].details["intensities"] == intensities
    assert findings[0].details["mean"] == 1500.0


def test_a_non_uniform_negative_face_pressure_keeps_the_sign_flip():
    fem = _quad_fem(1)
    step, _ = _read(_beuslo(1, [-1000.0, -2000.0, -2000.0, -1000.0], side=SIDE_NEGATIVE), fem)

    (load,) = _loads(step)
    assert (load.magnitude, load.surface.el_face_index) == (1500.0, -1)


# --- the load records the reader still has no card for -----------------------------------------


def test_bnload_and_bgrav_are_named_with_their_counts():
    """The hole reading BEUSLO opens. Before it, a Sesam deck's loading did not reach the model at
    all and every caller knew it; now a deck of gravity plus a pressure reads as a model whose
    only loading is the pressure -- a wrong answer where there used to be an empty one. So the two
    records the writer emits and this reader still cannot read are counted and named."""
    fem = _quad_fem(1)
    bulk = (
        write_ff("BNLOAD", [(1, 0, 0, 0), (1, 6, 0.0, 0.0), (-100.0, 0.0, 0.0, 0.0)])
        + write_ff("BNLOAD", [(1, 0, 0, 0), (2, 6, 0.0, 0.0), (-100.0, 0.0, 0.0, 0.0)])
        + write_ff("BGRAV", [(1, 0, 0, 0), (0.0, 0.0, -9.81)])
        + _beuslo(1, [Q] * 4)
    )
    step, findings = _read_all(bulk, fem)

    assert [ld.magnitude for ld in _loads(step)] == [Q], "the pressure still arrives"
    unread = {f.keyword: f.count for f in findings if f.kind == conversion_report.OMITTED}
    assert unread == {"BNLOAD": 2, "BGRAV": 1}


def test_they_are_named_even_when_the_deck_has_no_beuslo():
    """A deck with only a nodal load builds no step, and the load must still be named: this is the
    one case where the model comes back visibly empty, and it should say why."""
    fem = _quad_fem(1)
    step, findings = _read_all(write_ff("BGRAV", [(1, 0, 0, 0), (0.0, 0.0, -9.81)]), fem)

    assert step is None
    assert [(f.keyword, f.count) for f in findings] == [("BGRAV", 1)]

"""ada -> Sesam -> ada: a settlement and a pressure on both faces come back as they went in.

The two records the reader has just learned (BNDISPL, BEUSLO) are the last two the Sesam writer
emitted and nothing read, so this is the test that closes the asymmetry rather than describing
it: one model, ``to_fem(fem_format="sesam")``, ``ada.from_fem``, and the ``Bc`` and ``Load``
objects compared field by field -- magnitude, dofs, set membership, face, load case.

The pressure is applied on *both* faces because that is where the sign lives. BEUSLO cannot say
"which face" with SIDE (Sestra computes the load in the element's mid-plane and returns the
bit-identical result for SIDE 1, 2 and 3), so the writer puts the direction in the sign of
RLOAD: ``-q`` on the positive face, ``+q`` with SIDE 2 on the negative one (a positive RLOAD
pushes along the element's positive normal, which is *out of* the positive face). A reader that
ignored either half would give a plausible-looking model with the pressure pushing the wrong
way, which no deflection comparison inside one solver can see.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import Bc, Elem, FemSection, FemSet, Surface
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.read.read_loads import READ_STEP_NAME, SESAM_LOAD_CASE
from ada.fem.loads import LoadPressure
from ada.fem.shapes.definitions import ShellShapes
from ada.fem.steps import StepImplicitStatic

Q_POS = 1000.0
Q_NEG = 2500.0
SETTLEMENT = {1: 0.01, 3: -0.02}


def _model() -> ada.Assembly:
    """Two shells sharing an edge: one clamped end, one settling node, a pressure on each face."""
    a = ada.Assembly("A")
    p = a.add_part(ada.Part("P"))
    mat = p.add_material(ada.Material("S355"))
    fem = p.fem
    nodes = [ada.Node((float(i % 3), float(i // 3), 0.0), i + 1) for i in range(6)]
    for node in nodes:
        fem.nodes.add(node)
    els = [
        Elem(1, [nodes[0], nodes[1], nodes[4], nodes[3]], ShellShapes.QUAD, parent=fem),
        Elem(2, [nodes[1], nodes[2], nodes[5], nodes[4]], ShellShapes.QUAD, parent=fem),
    ]
    fem.elements = FemElements(els, fem_obj=fem)
    all_shells = fem.add_set(FemSet("SHELLS", els, "elset", parent=fem))
    fem.add_section(FemSection("sh", "shell", all_shells, mat, thickness=0.01, parent=fem))

    fem.add_bc(Bc("clamp", fem.add_set(FemSet("FIX", [nodes[0], nodes[3]], "nset", parent=fem)), [1, 2, 3, 4, 5, 6]))
    fem.add_bc(
        Bc(
            "settlement",
            fem.add_set(FemSet("SETTLE", [nodes[2]], "nset", parent=fem)),
            list(SETTLEMENT),
            magnitudes=list(SETTLEMENT.values()),
        )
    )

    step = fem.add_step(StepImplicitStatic("static"))
    top = fem.add_set(FemSet("TOP", [els[0]], "elset", parent=fem))
    btm = fem.add_set(FemSet("BTM", [els[1]], "elset", parent=fem))
    step.add_load(LoadPressure("p_pos", Q_POS, Surface("POS", "ELEMENT", top, el_face_index=1, parent=fem)))
    step.add_load(LoadPressure("p_neg", Q_NEG, Surface("NEG", "ELEMENT", btm, el_face_index=-1, parent=fem)))
    return a


@pytest.fixture(scope="module")
def round_tripped(tmp_path_factory) -> ada.FEM:
    """``_model()``'s FEM after a trip through a Sesam deck."""
    d = tmp_path_factory.mktemp("sesam_loads")
    _model().to_fem("m", fem_format="sesam", scratch_dir=d, overwrite=True)
    back = ada.from_fem(next(d.rglob("mT1.FEM")), "sesam")
    return list(back.parts.values())[0].fem


def _bc(fem: ada.FEM, *node_ids: int) -> Bc:
    """The one ``Bc`` on exactly those nodes. Sesam has no name for a boundary condition, so the
    reader names it after its node set; the nodes are what identifies it."""
    ids = set(node_ids)
    found = [bc for bc in fem.bcs if {m.id for m in bc.fem_set.members} == ids]
    assert len(found) == 1, f"{ids}: {[(b.name, [m.id for m in b.fem_set.members]) for b in fem.bcs]}"
    return found[0]


def _load(fem: ada.FEM, face: int):
    """The one pressure load on that shell face."""
    loads = [ld for st in fem.steps for ld in st.loads if ld.surface.el_face_index == face]
    assert (
        len(loads) == 1
    ), f"face {face}: {[(ld.name, ld.surface.el_face_index) for st in fem.steps for ld in st.loads]}"
    return loads[0]


def test_the_settlement_comes_back_with_its_magnitudes(round_tripped):
    """The defect this closes: FIX code 2 was read and BNDISPL was not, so the settling node came
    back clamped and the deck described a rigid support."""
    bc = _bc(round_tripped, 3)

    assert bc.dofs == list(SETTLEMENT)
    assert bc.magnitudes == list(SETTLEMENT.values())
    assert bc.fem_set.name == "SETTLE", "on the deck's own node set"
    assert bc.type == Bc.TYPES.DISPL


def test_the_settlement_records_the_load_case_it_was_written_into(round_tripped):
    """A prescribed displacement is loading in Sesam and a ``Bc`` belongs to no load case, so the
    case name survives only as metadata -- which is the writer's own approximation in reverse."""
    assert _bc(round_tripped, 3).metadata[SESAM_LOAD_CASE] == "LC1"


def test_the_plain_support_is_untouched(round_tripped):
    """The other half of the pairing: a BC with no magnitude must not gain one."""
    bc = _bc(round_tripped, 1, 4)

    assert bc.dofs == [1, 2, 3, 4, 5, 6]
    assert bc.magnitudes == [None] * 6
    assert SESAM_LOAD_CASE not in bc.metadata


def test_the_positive_face_pressure_comes_back_positive_on_the_positive_face(round_tripped):
    """The deck carries ``-1000`` and SIDE 1, and what went in was ``+1000`` on ``SPOS``."""
    load = _load(round_tripped, 1)

    assert load.type == load.TYPES.PRESSURE
    assert load.magnitude == Q_POS
    assert [m.id for m in load.surface.fem_set.members] == [1]
    assert load.surface.fem_set.name == "TOP"


def test_the_negative_face_pressure_comes_back_positive_on_the_negative_face(round_tripped):
    """The sign, both halves at once: the deck carries ``+2500`` and SIDE 2, and what went in was
    ``+2500`` on ``SNEG`` -- the face the model named survives in SIDE alone here, since the sign
    is unchanged."""
    load = _load(round_tripped, -1)

    assert load.magnitude == Q_NEG
    assert [m.id for m in load.surface.fem_set.members] == [2]
    assert load.surface.fem_set.name == "BTM"


def test_both_pressures_are_in_the_load_case_the_deck_names(round_tripped):
    """The writer opens ``LC1`` for a step with no load cases of its own
    (``write_loads.DEFAULT_CASE``) and names it with a TDLOAD record; both loads are in it."""
    (step,) = round_tripped.steps

    assert step.name == READ_STEP_NAME
    assert list(step.load_cases) == ["LC1"]
    assert sorted(ld.magnitude for ld in step.load_cases["LC1"].loads) == [Q_POS, Q_NEG]


def test_nothing_else_appeared_or_went_missing(round_tripped):
    """Two BCs and two loads, not three of either: the grouping must not split a load per element
    or a BC per node, and the reader must not invent a set-sized second copy of anything."""
    assert len(round_tripped.bcs) == 2
    assert sum(len(st.loads) for st in round_tripped.steps) == 2
    assert len(round_tripped.steps) == 1


def test_a_second_trip_through_the_deck_changes_nothing(round_tripped, tmp_path):
    """The load block is a fixed point: the reader's sign convention being the writer's inverse
    is only really pinned by writing the read model back out and getting the same records."""

    def load_block(text: str) -> list[str]:
        out, keep = [], False
        for line in text.splitlines():
            if line.startswith(("TDLOAD", "BEUSLO", "BNDISPL", "BNBCD")):
                keep = True
                out.append(line)
            elif keep and line.startswith(" " * 8):
                out.append(line)
            else:
                keep = False
        return out

    first = tmp_path / "first"
    _model().to_fem("m", fem_format="sesam", scratch_dir=first, overwrite=True)
    deck = next(first.rglob("mT1.FEM"))

    second = tmp_path / "second"
    ada.from_fem(deck, "sesam").to_fem("m", fem_format="sesam", scratch_dir=second, overwrite=True)

    assert load_block(next(second.rglob("mT1.FEM")).read_text()) == load_block(deck.read_text())

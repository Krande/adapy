"""A prescribed displacement is written: BNBCD FIX code 2 and a BNDISPL record with its value.

``write_bcs.PRESCRIBED`` was defined and never used -- "ada's Bc magnitudes are not carried into
BNDISPL yet" -- so a ``Bc`` built with ``magnitudes`` came out of the Sesam writer as an ordinary
clamp. A 20 mm settlement became a rigid support: a different structure, and one whose reaction
forces (the thing a settlement case is usually run for) are not the model's.

The layout was measured against Sestra V11.3-00, not guessed; ``write_bcs.bndispl_str`` records
the three runs. The short version: FIX code 2 says which dofs are prescribed, the BNDISPL record
in a load case says by how much, and with either card alone the settlement does not happen.
``tests/fem/test_sesam_prescribed_displacement.py`` is the solved cantilever.
"""

from __future__ import annotations

import ada
from ada.base.types import GeomRepr
from ada.fem import Bc, Elem, FemSection, FemSet
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.write.write_bcs import (
    FIXED,
    FREE,
    PRESCRIBED,
    bnbcd_str,
    prescribed_displacements,
)
from ada.fem.formats.sesam.write.write_loads import step_loads_str
from ada.fem.formats.sesam.write.write_utils import write_ff
from ada.fem.loads import LoadPoint
from ada.fem.steps import StepImplicitStatic
from ada.materials.metals import CarbonSteel


def _model(*, settle=-0.02, load=True) -> ada.Assembly:
    """A 1x1 quad shell: node 1 clamped, node 3 given a settlement, optionally a tip load."""
    a = ada.Assembly("a")
    p = a.add_part(ada.Part("p"))
    mat = p.add_material(ada.Material("S355", CarbonSteel("S355")))
    fem = p.fem
    coords = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    nodes = [fem.nodes.add(ada.Node(c, i, parent=fem)) for i, c in enumerate(coords, start=1)]
    els = fem.add_set(FemSet("plate", [fem.add_elem(Elem(1, nodes, "QUAD", parent=fem))], "elset", parent=fem))
    fem.add_section(FemSection("sec", GeomRepr.SHELL, els, mat, thickness=0.01, parent=fem))

    root = fem.add_set(FemSet("root", [nodes[0]], "nset", parent=fem))
    settled = fem.add_set(FemSet("settled", [nodes[2]], "nset", parent=fem))
    fem.add_bc(Bc("clamp", root, [1, 2, 3, 4, 5, 6]))
    fem.add_bc(Bc("settle", settled, [3], magnitudes=[settle]))

    step = a.fem.add_step(StepImplicitStatic("static", total_time=1.0, init_incr=1.0, max_incr=1.0))
    if load:
        step.add_load(LoadPoint("tip_load", 100.0, settled, [0, 1, 0, 0, 0, 0]))
    return a


def _bnbcd_codes(text: str) -> dict[int, list[int]]:
    out = {}
    for m in cards.re_bnbcd.finditer(text):
        d = m.groupdict()
        out[int(float(d["nodeno"]))] = [int(float(x)) for x in d["content"].split()]
    return out


def test_a_bc_with_a_magnitude_is_prescribed_not_fixed():
    a = _model()
    fems = [a.parts["p"].fem, a.fem]
    prescribed = prescribed_displacements(fems)

    assert prescribed == {3: {3: -0.02}}

    codes = _bnbcd_codes(bnbcd_str(fems, prescribed=prescribed))
    assert codes[1] == [FIXED] * 6  # the clamp is untouched
    assert codes[3] == [FREE, FREE, PRESCRIBED, FREE, FREE, FREE]


def test_a_magnitude_of_zero_stays_a_fixed_support():
    """A prescribed zero *is* a clamp. Writing it as code 2 plus a BNDISPL of 0.0 would change
    the text of every deck that has one without changing the model it describes."""
    a = _model(settle=0.0)
    fems = [a.parts["p"].fem, a.fem]

    assert prescribed_displacements(fems) == {}
    assert _bnbcd_codes(bnbcd_str(fems))[3] == [FREE, FREE, FIXED, FREE, FREE, FREE]


def test_the_bndispl_record_carries_the_value_in_the_load_case():
    a = _model()
    fems = [a.parts["p"].fem, a.fem]
    prescribed = prescribed_displacements(fems)

    text = step_loads_str(a.fem.steps[0], None, prescribed)

    # LLC 1, DTYPE 1 (displacement), COMPLX 0; then NODENO 3, NDOF 6 and the six values.
    assert write_ff("BNDISPL", [(1, 1, 0, 0), (3, 6, 0.0, 0.0), (-0.02, 0.0, 0.0, 0.0)]) in text
    # In the deck's one load case, beside the BNLOAD of the same case, not instead of it.
    assert text.index("TDLOAD") < text.index("BNLOAD") < text.index("BNDISPL")


def test_a_settlement_with_no_other_loading_still_opens_a_load_case():
    """BNDISPL declares an LLC, so a settlement is loading in Sesam. With no load case at all
    Sestra V11.3-00 answers "No load is specified" and writes no displacement result."""
    a = _model(load=False)
    prescribed = prescribed_displacements([a.parts["p"].fem, a.fem])

    text = step_loads_str(a.fem.steps[0], None, prescribed)

    assert "TDLOAD" in text and "BNDISPL" in text
    assert "BNLOAD" not in text


def test_a_prescribed_displacement_is_a_note_not_an_unreported_change(tmp_path):
    """End to end: the deck holds both cards, and the report says where the settlement went
    rather than claiming its value was dropped."""
    a = _model()

    with conversion_report.collect() as report:
        a.to_fem("presc", "sesam", scratch_dir=tmp_path, overwrite=True)

    deck = (tmp_path / "presc" / "prescT1.FEM").read_text()
    assert "BNDISPL" in deck
    assert _bnbcd_codes(deck)[3] == [FREE, FREE, PRESCRIBED, FREE, FREE, FREE]

    notes = [f for f in report.of_kind(conversion_report.NOTE) if f.subject == "settle"]
    assert len(notes) == 1
    assert notes[0].details["magnitudes"] == [-0.02]
    # Nothing is approximated or omitted about it any more.
    assert not [f for f in report.findings if f.subject == "settle" and f.kind != conversion_report.NOTE]

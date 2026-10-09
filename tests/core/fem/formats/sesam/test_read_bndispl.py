"""The Sesam reader and BNDISPL -- the value of a prescribed displacement.

The writer learned to emit a settlement as two cards: BNBCD FIX code 2 says *which* DOFs are
prescribed and a BNDISPL record in a load case says *by how much* (``write_bcs``, verified
against Sestra V11.3-00). The reader read only the first half, so a settlement came back as a
rigid clamp -- a different structure, and one whose reaction forces are what a settlement case
is usually run for.

These tests are on hand-written minimal decks, so each one says exactly which field it is
about. The pairing rule they pin is Sestra's own, measured in
``tests/fem/test_sesam_prescribed_displacement.py``:

* FIX code 2 **and** a BNDISPL record -> the node moves by the value.
* FIX code 2, no BNDISPL -> "WARNING ... No load is specified", no displacement result at all.
* a BNDISPL value on a DOF with FIX code 1 -> the value is ignored, the node does not move.

so the reader reproduces all three rather than reading every BNDISPL value it sees.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.read.read_constraints import get_bcs
from ada.fem.formats.sesam.read.read_loads import (
    SESAM_LOAD_CASE,
    STAGE,
    load_case_names,
)
from ada.fem.formats.sesam.write.write_bcs import (
    DTYPE_DISPLACEMENT,
    FIXED,
    FREE,
    PRESCRIBED,
)
from ada.fem.formats.sesam.write.write_utils import write_ff

#: Six values is what a node with all six DOFs carries, which is every node in here.
NDOF = 6


def _fem_with_nodes(*node_ids: int) -> ada.FEM:
    nodes = [ada.Node((float(i), 0, 0), nid) for i, nid in enumerate(node_ids)]
    return ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))


def _bnbcd(node_id: int, codes) -> str:
    codes = list(codes)
    return write_ff("BNBCD", [(node_id, len(codes), codes[0], codes[1]), tuple(codes[2:])])


def _bndispl(node_id: int, values, llc: int = 1, dtype: int = DTYPE_DISPLACEMENT, complx: int = 0) -> str:
    """One BNDISPL record, written the way ``write_bcs.bndispl_str`` writes it."""
    values = list(values)
    return write_ff(
        "BNDISPL",
        [(llc, dtype, complx, 0), (node_id, len(values)) + tuple(values[:2]), tuple(values[2:])],
    )


def _tdload(llc: int, name: str) -> str:
    return write_ff("TDLOAD", [(4, llc, 100 + len(name), 0), (name,)])


def _codes(**per_dof) -> list[int]:
    """Six FIX codes, ``d3=PRESCRIBED`` naming DOF 3."""
    out = [FREE] * NDOF
    for key, code in per_dof.items():
        out[int(key[1:]) - 1] = code
    return out


def _values(**per_dof) -> list[float]:
    out = [0.0] * NDOF
    for key, value in per_dof.items():
        out[int(key[1:]) - 1] = value
    return out


def _read(bulk: str, *node_ids: int):
    """``(bcs, findings)`` for a deck of ``bulk`` over a FEM holding ``node_ids``."""
    fem = _fem_with_nodes(*node_ids)
    with conversion_report.collect() as rep:
        bcs = get_bcs(bulk, fem)
    return bcs, [f for f in rep.findings if f.stage == STAGE]


def _findings(findings, kind: str) -> list:
    return [f for f in findings if f.kind == kind and f.keyword == "BNDISPL"]


# --- the pairing -----------------------------------------------------------------------------


def test_a_prescribed_dof_gets_its_bndispl_value():
    """The defect: dofs 1 and 3 prescribed, and only BNBCD was read, so both came back as a
    clamp with no magnitude at all."""
    bulk = _bnbcd(3, _codes(d1=PRESCRIBED, d3=PRESCRIBED)) + _bndispl(3, _values(d1=0.01, d3=-0.02))
    bcs, findings = _read(bulk, 3)

    assert len(bcs) == 1
    bc = bcs[0]
    assert bc.dofs == [1, 3]
    assert bc.magnitudes == [0.01, -0.02]
    assert not findings, "a settlement paired with its value is not a finding"


def test_a_fixed_dof_of_the_same_record_keeps_no_magnitude():
    """FIX code 1 is a support and carries no value; the record lists a zero for it either way,
    and reading that as a prescribed zero would be a statement the deck did not make."""
    bulk = _bnbcd(4, _codes(d1=FIXED, d2=FIXED, d3=PRESCRIBED)) + _bndispl(4, _values(d3=-0.005))
    (bc,), _ = _read(bulk, 4)

    assert bc.dofs == [1, 2, 3]
    assert bc.magnitudes == [None, None, -0.005]


def test_a_deck_with_no_bndispl_is_unchanged():
    """Every deck read before this change has no BNDISPL record; ``Bc.magnitudes`` must be what
    ``Bc`` fills in on its own, and nothing may be reported."""
    bcs, findings = _read(_bnbcd(1, _codes(d1=FIXED, d2=FIXED, d3=FIXED)), 1)

    assert len(bcs) == 1
    assert bcs[0].dofs == [1, 2, 3]
    assert bcs[0].magnitudes == [None, None, None]
    assert SESAM_LOAD_CASE not in bcs[0].metadata
    assert not findings


# --- the two halves disagreeing ---------------------------------------------------------------


def test_fix_code_2_with_no_bndispl_is_a_zero_and_a_note():
    """Sestra's "No load is specified": the run produces no displacement result at all, so the
    honest reading of the deck on its own is a support that does not move. Said out loud,
    because the deck asked for a settlement and does not describe one."""
    bcs, findings = _read(_bnbcd(2, _codes(d3=PRESCRIBED)), 2)

    assert bcs[0].dofs == [3]
    assert bcs[0].magnitudes == [0.0]
    notes = _findings(findings, conversion_report.NOTE)
    assert len(notes) == 1
    assert notes[0].details["dofs"] == [3]
    assert "No load is specified" in notes[0].reason


def test_a_value_on_a_dof_without_code_2_is_ignored_and_named():
    """Measured: with FIX code 1 instead of 2 the tip stayed at Z = 0.0 -- Sestra ignores the
    value. So does the reader, and it says which dof and which value it dropped."""
    bulk = _bnbcd(5, _codes(d1=FIXED, d3=FIXED)) + _bndispl(5, _values(d3=-0.02))
    bcs, findings = _read(bulk, 5)

    assert bcs[0].magnitudes == [None, None]
    omitted = _findings(findings, conversion_report.OMITTED)
    assert len(omitted) == 1
    assert omitted[0].details == {"dofs": [3], "values": [-0.02]}


# --- refusals, by name -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, field, value",
    [
        ({"dtype": 3}, "dtype", 3),
        ({"complx": 1}, "complx", 1),
    ],
)
def test_dtype_3_and_complx_1_are_refused_by_name(kwargs, field, value):
    """DTYPE 3 is an *acceleration* ("Supported values are 1 (displacement) and 3
    (acceleration)", Sestra's own diagnostic) and COMPLX 1 a phase-shifted load. Neither is a
    displacement, and ``Bc.magnitudes`` is what every other format writer emits as one."""
    bulk = _bnbcd(6, _codes(d3=PRESCRIBED)) + _bndispl(6, _values(d3=-0.02), **kwargs)
    bcs, findings = _read(bulk, 6)

    omitted = _findings(findings, conversion_report.OMITTED)
    assert len(omitted) == 1, [f.reason for f in findings]
    assert omitted[0].details[field] == value
    assert field.upper() in omitted[0].reason
    # ... and the value did not arrive anyway: the dof is prescribed with nothing behind it.
    assert bcs[0].magnitudes == [0.0]
    assert _findings(findings, conversion_report.NOTE), "and the empty code 2 is the note it is"


def test_a_short_record_is_refused_by_name():
    """NDOF says how many values follow. A record declaring six and carrying four is malformed,
    and padding it with zeros would invent a prescribed zero on the missing dofs."""
    short = write_ff("BNDISPL", [(1, DTYPE_DISPLACEMENT, 0, 0), (7, NDOF, 0.0, 0.0), (0.0, -0.02)])
    bcs, findings = _read(_bnbcd(7, _codes(d3=PRESCRIBED)) + short, 7)

    omitted = _findings(findings, conversion_report.OMITTED)
    assert len(omitted) == 1
    assert omitted[0].details == {"ndof": NDOF, "n_values": 4}
    assert bcs[0].magnitudes == [0.0]


# --- the load case ---------------------------------------------------------------------------


def test_the_load_case_the_record_names_is_recorded_on_the_bc():
    """A settlement is *loading* in Sesam -- BNDISPL declares an LLC -- and an ada ``Bc`` belongs
    to no load case, so the name has nowhere to go but the metadata."""
    bulk = _tdload(2, "SETTLEMENT") + _bnbcd(8, _codes(d3=PRESCRIBED)) + _bndispl(8, _values(d3=-0.02), llc=2)
    (bc,), _ = _read(bulk, 8)

    assert bc.metadata[SESAM_LOAD_CASE] == "SETTLEMENT"


def test_an_unnamed_load_case_is_lc_plus_its_number():
    """No TDLOAD for the case: ``LC<llc>``, which is also the name the writer gives the one case
    it invents (``write_loads.DEFAULT_CASE``), so the name still round-trips."""
    bulk = _bnbcd(9, _codes(d3=PRESCRIBED)) + _bndispl(9, _values(d3=-0.02), llc=1)
    (bc,), _ = _read(bulk, 9)

    assert bc.metadata[SESAM_LOAD_CASE] == "LC1"


def test_two_load_cases_prescribing_one_node_are_two_bcs_each_naming_its_case():
    """Sestra solves each load case on its own, and GeniE prescribes one support differently in
    each. Only the first case used to be read (reported as approximated) and the second case's
    settlement was lost; now each case is a ``Bc`` of its own, as the writer takes them."""
    bulk = (
        _tdload(1, "CASE_A")
        + _tdload(2, "CASE_B")
        + _bnbcd(10, _codes(d3=PRESCRIBED))
        + _bndispl(10, _values(d3=-0.02), llc=2)
        + _bndispl(10, _values(d3=-0.01), llc=1)
    )
    bcs, findings = _read(bulk, 10)

    assert [(bc.metadata[SESAM_LOAD_CASE], bc.magnitudes) for bc in bcs] == [("CASE_A", [-0.01]), ("CASE_B", [-0.02])]
    assert [m.id for bc in bcs for m in bc.fem_set.members] == [10, 10]
    assert _findings(findings, conversion_report.APPROXIMATED) == []


def test_genies_two_case_settlement_reads_back_and_writes_back_case_by_case(tmp_path):
    """GeniE V8.13-02's own deck of the frames fixture: Sp_presc at (14, 0, 0) prescribed dx, dz, rz, with -0.003 in
    dx in LC1 and 0.005, -0.01, 0.001 in LC2. Read, both cases come back; written again, both BNDISPL records are
    GeniE's."""
    import pathlib

    from ada.fem.formats.sesam.read.reader import read_fem

    files = pathlib.Path(__file__).resolve().parents[5] / "files" / "fem_files" / "sesam"
    deck = files / "genie_supports_frames_T1.FEM"
    a = ada.Assembly("frames") / read_fem(deck)
    (part,) = [p for p in a.get_all_subparts() if len(p.fem.nodes) > 0]
    (node,) = [n for n in part.fem.nodes if np.allclose(n.p, (14, 0, 0))]
    held = {
        bc.metadata.get(SESAM_LOAD_CASE): {
            d: None if m is None else round(m, 6) for d, m in zip(bc.dofs, bc.magnitudes)
        }
        for bc in part.fem.bcs
        if node in bc.fem_set.members
    }
    # The first case's Bc is the support (dy merely fixed); the second holds the prescribed dofs. GeniE wrote the
    # values in single precision (-0.00300000003).
    assert held == {"LC1": {1: -0.003, 2: None, 3: 0.0, 6: 0.0}, "LC2": {1: 0.005, 3: -0.01, 6: 0.001}}

    a.to_fem("back", "sesam", scratch_dir=tmp_path, overwrite=True)
    text = next((tmp_path / "back").glob("*T1.FEM")).read_text()
    names = load_case_names(text)
    records = [r.split() for r in text.split("BNDISPL")[1:]]
    values = sorted(
        (names[int(float(r[0]))], [round(float(x), 6) for x in r[6:12]]) for r in records if int(float(r[4])) == node.id
    )
    assert values == [("LC1", [-0.003, 0.0, 0.0, 0.0, 0.0, 0.0]), ("LC2", [0.005, 0.0, -0.01, 0.0, 0.0, 0.001])]


# --- grouping ---------------------------------------------------------------------------------


def test_two_nodes_settling_differently_do_not_share_one_bc():
    """A ``Bc`` carries one magnitude per dof. Grouping on the dof pattern alone -- which is
    what the reader did -- would put both nodes on one ``Bc`` and give them one settlement."""
    bulk = (
        _bnbcd(11, _codes(d3=PRESCRIBED))
        + _bnbcd(12, _codes(d3=PRESCRIBED))
        + _bndispl(11, _values(d3=-0.02))
        + _bndispl(12, _values(d3=-0.03))
    )
    bcs, _ = _read(bulk, 11, 12)

    assert len(bcs) == 2
    assert sorted(bc.magnitudes[0] for bc in bcs) == [-0.03, -0.02]
    assert sorted(m.id for bc in bcs for m in bc.fem_set.members) == [11, 12]


def test_two_nodes_settling_alike_share_one_bc_on_the_decks_own_set():
    """... and equal settlements still group, on the node set the deck names, exactly as two
    equal supports do."""
    fem = _fem_with_nodes(13, 14)
    fem.sets.add(ada.fem.FemSet("SETTLE", [fem.nodes.from_id(13), fem.nodes.from_id(14)], "nset", parent=fem))
    bulk = (
        _bnbcd(13, _codes(d3=PRESCRIBED))
        + _bnbcd(14, _codes(d3=PRESCRIBED))
        + _bndispl(13, _values(d3=-0.02))
        + _bndispl(14, _values(d3=-0.02))
    )
    bcs = get_bcs(bulk, fem)

    assert len(bcs) == 1
    assert bcs[0].name == "SETTLE"
    assert bcs[0].magnitudes == [-0.02]
    assert sorted(m.id for m in bcs[0].fem_set.members) == [13, 14]

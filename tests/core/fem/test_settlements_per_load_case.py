"""A prescribed displacement in each load case, and a rigid link's slave rotations, through the Sesam and Abaqus
writers.

GeniE gives one support a value in each load case (V8.13-02: -0.003 in dx in LC1; 0.005, -0.01, 0.001 in dx, dz, rz
in LC2), which adapy holds as one ``Bc`` per case naming the case in ``metadata[BC_LOAD_CASE]``. The Sesam deck takes
them as one BNDISPL per case; the Abaqus deck holds the dofs at zero in model data (Abaqus 2025: "PRESCRIBED
*BOUNDARY MAGNITUDES MUST BE ZERO IN THE MODEL DEFINITION") and gives the values in the step of the case.
"""

from __future__ import annotations

import re

import pytest

import ada
from ada.fem import Bc, Constraint, FemSet
from ada.fem.constraints import BC_LOAD_CASE
from ada.fem.formats import conversion_report
from ada.fem.steps import StepImplicitStatic
from ada.materials.metals import CarbonSteel


def _cantilever(with_steps=("LC1", "LC2"), tagged=True):
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - 2) < 1e-9]
    fem.add_bc(Bc("clamp", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    tip_set = fem.add_set(FemSet("tip_set", [tip], "nset"))
    fem.add_bc(Bc("tip", tip_set, [1, 3, 6]))
    cases = {"LC1": [-0.003, 0.0, 0.0], "LC2": [0.005, -0.01, 0.001]}
    for case, values in cases.items():
        meta = {BC_LOAD_CASE: case} if tagged else None
        fem.add_bc(Bc(f"tip_{case}", tip_set, [1, 3, 6], magnitudes=values, metadata=meta))
    for name in with_steps:
        a.fem.add_step(StepImplicitStatic(name, total_time=1.0, init_incr=1.0, max_incr=1.0))
    return a, tip.id


def _bndispl(deck: str) -> dict[str, dict[int, list[float]]]:
    """``{case name: {node: six values}}`` from the TDLOAD names and BNDISPL records."""
    names = {int(float(m[1])): m[2] for m in re.finditer(r"^TDLOAD\s+\S+\s+(\S+).*\n\s+(\S+)", deck, re.M)}
    out: dict[str, dict[int, list[float]]] = {}
    for m in re.finditer(r"^BNDISPL(.*\n(?: .*\n){2})", deck, re.M):
        v = [float(x) for x in m[1].split()]
        out.setdefault(names[int(v[0])], {})[int(v[4])] = v[6:12]
    return out


def test_each_load_case_gets_its_own_bndispl(tmp_path):
    a, tip = _cantilever(with_steps=())
    a.to_fem("presc", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "presc" / "prescT1.FEM").read_text()
    assert _bndispl(deck) == {
        "LC1": {tip: [-0.003, 0.0, 0.0, 0.0, 0.0, 0.0]},
        "LC2": {tip: [0.005, 0.0, -0.01, 0.0, 0.0, 0.001]},
    }
    # FIX code 2 on dx, dz, rz of the tip, 1 on dy
    assert re.search(
        rf"^BNBCD\s+{tip}\.0+E\+00\s+6\.0+E\+00\s+2\.0+E\+00\s+0\.0+E\+00\n\s+2\.0+E\+00\s+0\.0+E\+00\s+0\.0+E\+00\s+2\.0+E\+00",
        deck,
        re.M,
    )  # noqa: E501


def test_an_untagged_settlement_stays_in_the_first_case(tmp_path):
    a, tip = _cantilever(with_steps=(), tagged=False)
    with conversion_report.collect():
        a.to_fem("presc", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "presc" / "prescT1.FEM").read_text()
    assert list(_bndispl(deck)) == ["LC1"]


def test_abaqus_holds_the_dofs_in_model_data_and_values_them_per_step(tmp_path):
    a, _ = _cantilever()
    with conversion_report.collect() as report:
        a.to_fem("presc", "abaqus", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "presc" / "presc.inp").read_text()
    model, _, steps = inp.partition("*Step")
    # model data: no magnitude anywhere, and each tip dof held once
    tip_lines = re.findall(r"^ \S*tip_set, (\d), \1(.*)$", model, re.M)
    assert sorted(d for d, _ in tip_lines) == ["1", "3", "6"]
    assert all(rest == "" for _, rest in tip_lines)
    lc1, lc2 = steps.split("*Step")[0], steps.split("*Step")[1]
    assert re.findall(r"tip_set, (\d), \d, (\S+)", lc1) == [("1", "-0.003"), ("3", "0.0"), ("6", "0.0")]
    assert re.findall(r"tip_set, (\d), \d, (\S+)", lc2) == [("1", "0.005"), ("3", "-0.01"), ("6", "0.001")]
    assert not report.has_omissions


def test_abaqus_resets_another_cases_settlement_in_a_step_of_its_own(tmp_path):
    a, _ = _cantilever(with_steps=("LC1", "other"))
    with conversion_report.collect() as report:
        a.to_fem("presc", "abaqus", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "presc" / "presc.inp").read_text()
    other = inp.split("*Step, name=other")[1]
    assert re.findall(r"tip_set, (\d), \d, (\S+)", other) == [("1", "0."), ("3", "0."), ("6", "0.")]
    (finding,) = [f for f in report.findings if f.keyword == "*Boundary"]
    assert (finding.kind, finding.subject, finding.details["load_case"]) == ("omitted", "tip_LC2", "LC2")


def test_a_lone_settlement_is_held_in_model_data_and_valued_in_the_step(tmp_path):
    """The Sesam reader's shape: one ``Bc`` holding the dof and carrying its value. Abaqus refuses the value in model
    data (fatal: "PRESCRIBED *BOUNDARY MAGNITUDES MUST BE ZERO IN THE MODEL DEFINITION"), which adapy wrote there."""
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5, "line")
    (tip,) = [n for n in p.fem.nodes if abs(n.x - 1) < 1e-9]
    p.fem.add_bc(Bc("settle", p.fem.add_set(FemSet("tip_set", [tip], "nset")), [3], magnitudes=[-0.01]))
    a.fem.add_step(StepImplicitStatic("static", total_time=1.0, init_incr=1.0, max_incr=1.0))
    a.to_fem("lone", "abaqus", scratch_dir=tmp_path, overwrite=True)
    model, _, step = (tmp_path / "lone" / "lone.inp").read_text().partition("*Step")
    assert re.findall(r"^ \S*tip_set, 3, 3(.*)$", model, re.M) == [""]
    assert re.findall(r"^ \S*tip_set, 3, 3(.*)$", step, re.M) == [", -0.01"]


def _linked(rotation_dependent: bool):
    a = ada.Assembly("a") / (ada.Part("p") / ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01))
    (p,) = a.get_all_subparts()
    p.fem = p.to_fem_obj(0.5, use_quads=True)
    m = p.fem.nodes.add(ada.Node((0.5, 0.5, 1.0)))
    slaves = [n for n in p.fem.nodes if abs(n.z) < 1e-9 and n.x < 0.6]
    dofs = [1, 2, 3, 4, 5, 6] if rotation_dependent else [1, 2, 3]
    p.fem.add_constraint(
        Constraint("rl", "coupling", FemSet("rl_m", [m], "nset"), FemSet("rl_s", slaves, "nset"), dofs=dofs)
    )
    p.fem.add_bc(Bc("rl_bc", p.fem.add_set(FemSet("rl_bc_set", [m], "nset")), [1, 2, 3, 4, 5, 6]))
    return a, len(slaves)


@pytest.mark.parametrize("rotation_dependent, n_terms", [(True, 12), (False, 9)])
def test_a_couplings_slave_rotations_follow_the_master(tmp_path, rotation_dependent, n_terms):
    """GeniE's rotation dependent rigid link is a 12-term BLDEP with BNBCD 3 on all six slave dofs; translations only,
    9 terms and 3 on dofs 1-3. Read back, the coupling holds the same dofs."""
    a, n_slaves = _linked(rotation_dependent)
    with conversion_report.collect() as report:
        a.to_fem("rl", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "rl" / "rlT1.FEM").read_text()
    counts = [int(float(m[1])) for m in re.finditer(r"^BLDEP\s+\S+\s+\S+\s+\S+\s+(\S+)", deck, re.M)]
    assert counts == [n_terms] * n_slaves
    assert not [f for f in report.findings if f.keyword == "Constraint"]
    b = ada.from_fem(tmp_path / "rl" / "rlT1.FEM")
    (con,) = [c for p in b.get_all_parts_in_assembly() for c in p.fem.constraints.values()]
    assert list(con.dofs) == ([1, 2, 3, 4, 5, 6] if rotation_dependent else [1, 2, 3])


# --- a combination of cases that prescribe the same support -------------------------------------------------------


@pytest.fixture(scope="module")
def frames_combined(tmp_path_factory):
    """GeniE's frames fixture (``Sp_presc``: dx -0.003 in LC1; dx 0.005, dz -0.01, rz 0.001 in LC2) with
    ``LCC = LC1 + LC2`` and ``LCF = 2 LC1 + 0.5 LC2``, written to Sesam and Abaqus."""
    import pathlib
    import shutil

    from ada.fem.concept.loads import (
        LoadConceptCaseCombination,
        LoadConceptCaseFactored,
    )

    xml = pathlib.Path(__file__).resolve().parents[3] / "files" / "fem_files" / "sesam" / "genie_supports_frames.xml"
    work = tmp_path_factory.mktemp("frames_lcc")
    shutil.copy(xml, work / xml.name)
    with conversion_report.collect() as report:
        a = ada.from_genie_xml(work / xml.name)
        (part,) = a.get_all_subparts()
        loads = part.concept_fem.loads
        lc1, lc2 = loads.load_cases["LC1"], loads.load_cases["LC2"]
        for name, f1, f2 in (("LCC", 1.0, 1.0), ("LCF", 2.0, 0.5)):
            terms = [LoadConceptCaseFactored(lc1, f1), LoadConceptCaseFactored(lc2, f2)]
            loads.add_load_case_combination(LoadConceptCaseCombination(name, terms))
        part.fem = part.to_fem_obj(0.5, use_quads=True)
        a.to_fem("fr", "sesam", scratch_dir=work, overwrite=True)
        a.to_fem("fr_abq", "abaqus", scratch_dir=work, overwrite=True)
    (node,) = part.fem.sets.get_nset_from_name("Sp_presc_set").members
    deck = (work / "fr" / "frT1.FEM").read_text()
    inp = (work / "fr_abq" / "fr_abq.inp").read_text()
    return part, node.id, deck, inp, report


#: The factored sums on Sp_presc's dx, dz, rz: LC1 (-0.003, 0, 0), LC2 (0.005, -0.01, 0.001).
COMBINED = {"LCC": [0.002, -0.01, 0.001], "LCF": [-0.0035, -0.005, 0.0005]}


def test_a_combination_prescribes_its_cases_factored_sum_once(frames_combined):
    """One ``Bc`` per support and combination, its values the factored sum. It used to be one ``Bc`` per term, and the
    Sesam writer kept the last term's values (LCC dx 0.005, LC1's -0.003 lost, silently) and the Abaqus writer the
    first's (LCC dx -0.003, LC2's values reported omitted)."""
    part, _, _, _, _ = frames_combined
    from ada.fem.constraints import BC_LOAD_CASE

    for case, values in COMBINED.items():
        (bc,) = [b for b in part.fem.bcs if (b.metadata or {}).get(BC_LOAD_CASE) == case]
        assert (bc.name, bc.dofs) == (f"Sp_presc_{case}", [1, 3, 6])
        assert bc.magnitudes == pytest.approx(values, rel=1e-12)


def test_the_sesam_combination_case_holds_the_factored_sum(frames_combined):
    _, node, deck, _, report = frames_combined
    got = {case: values[node] for case, values in _bndispl(deck).items()}
    assert got["LC1"] == [-0.003, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert got["LC2"] == [0.005, 0.0, -0.01, 0.0, 0.0, 0.001]
    for case, (dx, dz, rz) in COMBINED.items():
        assert got[case] == pytest.approx([dx, 0.0, dz, 0.0, 0.0, rz], rel=1e-12, abs=0.0)
    assert not [f for f in report.findings if f.keyword in ("BNDISPL", "*Boundary") and f.kind == "omitted"]


def test_the_abaqus_combination_case_holds_the_factored_sum(frames_combined):
    """Measured with Abaqus 2025 on the Sestra tier's cantilever (``test_concept_supports_sestra``, the same values
    on a tip support, LCC = LC1 + LC2): before, LCC's tip came out at dx -0.003, dz 0, rz 0 (LC1's) and
    max |u_LCC - (u_LC1 + u_LC2)| = 0.01; after, dx 0.002, dz -0.01, rz 0.001 and 3.1e-10."""
    _, _, _, inp, _ = frames_combined
    cases = {m[1]: m[2] for m in re.finditer(r"^\*Load Case, name=(\S+)\n(.*?)^\*End Load Case", inp, re.M | re.S)}
    for case, values in COMBINED.items():
        got = re.findall(r"Sp_presc_set, (\d), \d, (\S+)", cases[case])
        assert [d for d, _ in got] == ["1", "3", "6"]
        assert [float(v) for _, v in got] == pytest.approx(values, rel=1e-12, abs=0.0)


def test_a_second_value_for_one_dof_in_one_case_is_reported_not_overwritten(tmp_path):
    """Two ``Bc`` prescribing the tip's dx in LC1, -0.003 and 0.004: the BNDISPL keeps the first and names the second.
    The Sesam writer used to keep the second without a word."""
    a, tip = _cantilever(with_steps=())
    (p,) = a.get_all_subparts()
    tip_set = p.fem.sets.get_nset_from_name("tip_set")
    p.fem.add_bc(Bc("tip_LC1_again", tip_set, [1], magnitudes=[0.004], metadata={BC_LOAD_CASE: "LC1"}))
    with conversion_report.collect() as report:
        a.to_fem("presc", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "presc" / "prescT1.FEM").read_text()
    assert _bndispl(deck)["LC1"] == {tip: [-0.003, 0.0, 0.0, 0.0, 0.0, 0.0]}
    (finding,) = [f for f in report.findings if f.keyword == "BNDISPL"]
    assert (finding.kind, finding.subject) == ("omitted", "tip_LC1_again")
    assert (finding.details["load_case"], finding.details["kept"], finding.details["nodes"]) == (
        "LC1",
        "tip_LC1",
        [tip],
    )

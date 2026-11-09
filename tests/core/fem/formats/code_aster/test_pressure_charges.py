"""Pressures in the Code_Aster deck: one charge for a case's pressures, which add where they overlap; their values in
the sidecar the ``.rmed`` reader gives as ``cases.pressures`` (``tests/fem/test_code_aster_pressure_cases.py``
solves).

* Each pressure's charge carries ``VERI_NORM='NON'``. The merge of a case's distributed loads into one charge
  (Code_Aster computes a beam model's stresses over one such charge, <CALCULEL2_92>) read it as an item acting on
  the whole model, so two pressures never shared a charge: a case of -50 kPa on one plate and -20 kPa on another got
  no stresses. It is a simple keyword, written once.
* In one charge a ``FORCE_COQUE`` item on a cell replaces an earlier one there, where two charges add. Pressures that
  load one element twice (two cases on one plate in a combination, or one pressure naming a set twice) are written
  as the net pressure on each element, over mesh groups of their own.
"""

from __future__ import annotations

import json
import re

import pytest

import ada
from ada.fem import Bc, FemSet
from ada.fem.concept.loads import (
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptSurface,
)
from ada.fem.formats import conversion_report
from ada.materials.metals import CarbonSteel


def panel(cases):
    """Two 2 x 2 m, 10 mm plates side by side in z = 0 with a FB100x10 stiffener along x, both ends clamped.
    ``cases``: ``{name: [(plate index, pressure)]}``, or ``{name: {case name: factor}}`` for a combination."""
    mat = ada.Material("S355", CarbonSteel("S355"))
    plates = [
        ada.Plate.from_3d_points(f"pl{i + 1}", [(x, 0, 0), (x + 2, 0, 0), (x + 2, 2, 0), (x, 2, 0)], 0.01, mat=mat)
        for i, x in enumerate((0.0, 2.0))
    ]
    st = ada.Beam("st", (0, 1, 0), (4, 1, 0), "FB100x10", mat, up=(0, 0, 1))
    p = ada.Part("P") / [*plates, st]
    a = ada.Assembly("A") / p
    made = {}
    for name, spec in cases.items():
        if isinstance(spec, dict):
            terms = [LoadConceptCaseFactored(made[c], f) for c, f in spec.items()]
            p.concept_fem.loads.add_load_case_combination(LoadConceptCaseCombination(name, terms))
            continue
        loads = [LoadConceptSurface(f"{name}_{k}", plates[i], pressure=pr) for k, (i, pr) in enumerate(spec)]
        made[name] = p.concept_fem.loads.add_load_case(LoadConceptCase(name, loads))
    p.fem = p.to_fem_obj(0.5, use_quads=True)
    ends = [n for n in p.fem.nodes if abs(n.x) < 1e-6 or abs(n.x - 4) < 1e-6]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("ends", ends, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    return a, p


def write(a, tmp_path, name="d"):
    with conversion_report.collect() as report:
        a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True)
    folder = tmp_path / name
    return (folder / f"{name}.comm").read_text(), report, folder


def charge(comm, concept) -> str:
    m = re.search(rf"^{concept} = AFFE_CHAR_MECA\((.*?)^\)", comm, re.M | re.S)
    assert m, f"no charge {concept}"
    return m[1]


def case_charges(comm) -> dict[str, list[str]]:
    return {
        name: [c for c in chs.split(",") if c]
        for name, chs in re.findall(r"_F\(NOM_CAS='([^']+)', CHAR_MECA=\(([^)]*)\)", comm)
    }


def pres_items(text) -> list[tuple[str, float]]:
    return [(g, float(p)) for g, p in re.findall(r"_F\(GROUP_MA='([^']+)', PRES=([-0-9.e+]+)\)", text)]


def stress_omissions(report):
    return [f.subject for f in report.findings if f.keyword == "CALC_CHAMP"]


def test_two_pressures_of_different_values_share_one_charge(tmp_path):
    a, _ = panel({"LC": [(0, 50e3), (1, 20e3)]})
    comm, report, _ = write(a, tmp_path)
    (chs,) = case_charges(comm).values()
    assert len(chs) == 1, "the two pressures are not one charge"
    body = charge(comm, chs[0])
    assert body.count("VERI_NORM") == 1 and "VERI_NORM='NON'" in body
    assert sorted(p for _, p in pres_items(body)) == [-50e3, -20e3]
    assert stress_omissions(report) == []  # every case's stresses are computed


def test_a_combination_of_two_cases_on_disjoint_plates_shares_one_charge(tmp_path):
    a, _ = panel({"LC1": [(0, 50e3)], "LC2": [(1, 20e3)], "ULS": {"LC1": 1.3, "LC2": 1.0}})
    comm, report, _ = write(a, tmp_path)
    chs = case_charges(comm)["ULS"]
    assert len(chs) == 1
    assert sorted(p for _, p in pres_items(charge(comm, chs[0]))) == [-65e3, -20e3]
    assert stress_omissions(report) == []


def test_two_pressures_on_one_plate_add_in_the_charge(tmp_path):
    """Two cases of 50 and 30 kPa on one plate combined: one charge of 80 kPa on the plate's elements, over a group of
    the deck's own that the MED file holds."""
    a, p = panel({"LC1": [(0, 50e3)], "LC3": [(0, 30e3)], "C13": {"LC1": 1.0, "LC3": 1.0}})
    comm, report, folder = write(a, tmp_path)
    chs = case_charges(comm)["C13"]
    assert len(chs) == 1
    (item,) = pres_items(charge(comm, chs[0]))
    group, pres = item
    assert pres == pytest.approx(-80e3)
    assert stress_omissions(report) == []
    import h5py

    with h5py.File(folder / "d.med") as f:
        names = {bytes(r).rstrip(b"\0").decode().strip() for g in f["FAS/d/ELEME"].values() for r in g["GRO/NOM"][()]}
    assert group in names
    assert group not in p.fem.elsets, "the deck's group was left on the model"


def test_pressures_that_cancel_load_nothing(tmp_path):
    a, _ = panel({"LC1": [(0, 50e3), (1, 20e3)], "LC2": [(0, -50e3)], "C": {"LC1": 1.0, "LC2": 1.0}})
    comm, _, _ = write(a, tmp_path)
    (ch,) = case_charges(comm)["C"]
    assert [p for _, p in pres_items(charge(comm, ch))] == [pytest.approx(-20e3)]


def test_one_pressure_naming_a_set_twice_is_applied_twice(tmp_path):
    from ada.fem import LoadPressure, StepImplicitStatic, Surface

    a, p = panel({})
    plate1 = [el for el in p.fem.elements.shell if max(n.x for n in el.nodes) <= 2 + 1e-6]
    fs = p.fem.add_set(FemSet("pl1_els", plate1, FemSet.TYPES.ELSET))
    surf = p.fem.add_surface(Surface("twice", Surface.TYPES.ELEMENT, [fs, fs], el_face_index=[1, 1], parent=p.fem))
    step = a.fem.add_step(StepImplicitStatic("static"))
    step.add_load(LoadPressure("twice", 1000.0, surf))
    comm, _, _ = write(a, tmp_path)
    (load_concept,) = re.findall(r"^(\w+) = AFFE_CHAR_MECA\(\s*MODELE=model,\s*VERI_NORM", comm, re.M)
    assert [p for _, p in pres_items(charge(comm, load_concept))] == [pytest.approx(2000.0)]


def test_the_sidecar_holds_each_cases_net_shell_pressures(tmp_path):
    """In Sesam's sign (a SIN's BEUSLO): positive along the element's normal. The positive face's pressure pushes
    against it."""
    a, p = panel({"LC1": [(0, 50e3)], "LC3": [(0, 30e3)], "C13": {"LC1": 1.0, "LC3": 1.0}})
    _, _, folder = write(a, tmp_path)
    side = json.loads((folder / "d.adapy_fem.json").read_text())
    assert side["version"] >= 7
    cases = side["shell_pressures"]["result"]
    assert sorted(cases) == ["1", "2", "3"]
    n_plate = len(cases["1"])
    assert n_plate == len([el for el in p.fem.elements.shell if max(n.x for n in el.nodes) <= 2 + 1e-6])
    for k, value in (("1", 50e3), ("2", 30e3), ("3", 80e3)):
        assert set(cases[k]) == set(cases["1"])
        assert sorted({round(abs(v), 6) for v in cases[k].values()}) == [value]


def test_charges_that_give_a_simple_keyword_different_values_are_not_merged(monkeypatch):
    from ada.fem.formats.code_aster.write.steps import static_lin

    a = "x = AFFE_CHAR_MECA(MODELE=model, VERI_NORM='NON', FORCE_COQUE=(_F(GROUP_MA='a', PRES=1.0),))"
    b = "y = AFFE_CHAR_MECA(MODELE=model, VERI_NORM='OUI', FORCE_COQUE=(_F(GROUP_MA='b', PRES=1.0),))"
    assert static_lin._charge_keywords(a) == [("VERI_NORM", "'NON'"), ("FORCE_COQUE", ["_F(GROUP_MA='a', PRES=1.0)"])]

    class Fake:
        type = "pressure"

        class TYPES:
            PRESSURE = "pressure"

        def __init__(self, text):
            self.text = text

    monkeypatch.setattr(static_lin, "write_load", lambda ld: ld.text)
    merged = static_lin.merged_charge_str("m", [Fake(a), Fake(a.replace("'a'", "'c'"))])
    assert merged.count("VERI_NORM") == 1
    assert static_lin.merged_charge_str("m", [Fake(a), Fake(b)]) is None


def _med_groups(med) -> set[str]:
    import h5py

    with h5py.File(med) as f:
        root = f["FAS"][next(iter(f["FAS"]))]["ELEME"]
        return {bytes(r).rstrip(b"\0").decode().strip() for g in root.values() for r in g["GRO/NOM"][()]}


def test_every_net_pressure_group_the_deck_names_is_in_the_mesh(tmp_path):
    """The deck's net-pressure groups and the mesh's come from two passes over the same loads: pinned equal over
    several cases, combinations, a general step and a pressure naming its set twice."""
    from ada.fem import LoadPressure, StepImplicitStatic, Surface

    a, p = panel(
        {
            "LC1": [(0, 50e3)],
            "LC3": [(0, 30e3), (1, 20e3)],
            "LC4": [(1, -20e3)],
            "C13": {"LC1": 1.0, "LC3": 1.0},
            "C134": {"LC1": 1.0, "LC3": 1.0, "LC4": 1.0},
            "C33": {"LC3": 2.0, "LC1": 0.5},
        }
    )
    plate1 = [el for el in p.fem.elements.shell if max(n.x for n in el.nodes) <= 2 + 1e-6]
    fs = p.fem.add_set(FemSet("pl1_els", plate1, FemSet.TYPES.ELSET))
    surf = p.fem.add_surface(Surface("twice", Surface.TYPES.ELEMENT, [fs, fs], el_face_index=[1, -1], parent=p.fem))
    step = a.fem.add_step(StepImplicitStatic("general"))
    step.add_load(LoadPressure("g_twice", 1000.0, surf))
    step.add_load(
        LoadPressure(
            "g_once",
            500.0,
            p.fem.add_surface(Surface("once", Surface.TYPES.ELEMENT, fs, el_face_index=1, parent=p.fem)),
        )
    )
    comm, _, folder = write(a, tmp_path)
    named = set(re.findall(r"GROUP_MA='(P[0-9a-f]{8}_\d+)'", comm))
    assert len(named) >= 4
    assert named <= _med_groups(folder / "d.med")
    assert not [n for n in p.fem.elsets if re.fullmatch(r"P[0-9a-f]{8}_\d+", n)]


def test_a_group_name_the_mesh_already_has_is_refused_and_nothing_is_left_behind(tmp_path):
    from ada.fem.exceptions.model_definition import UnsupportedLoadType
    from ada.fem.formats.code_aster.write.write_loads import (
        add_pressure_groups,
        net_pressure_groups,
    )
    from ada.fem.formats.code_aster.write.write_steps import all_steps

    a, p = panel({"LC1": [(0, 50e3)], "LC3": [(0, 30e3), (1, 20e3)], "C13": {"LC1": 1.0, "LC3": 1.0}})
    steps = all_steps(a)
    (lc,) = [lc for st in steps for lc in st.load_cases.values() if lc.name == "C13"]
    groups = net_pressure_groups([ld for ld in lc.loads if ld.type == ld.TYPES.PRESSURE])
    assert len(groups) == 2
    taken = groups[-1][1]  # the later group: the earlier one would have been added first
    p.fem.add_set(FemSet(taken, groups[-1][2], FemSet.TYPES.ELSET))
    before = set(p.fem.elsets)
    with pytest.raises(UnsupportedLoadType, match=taken):
        add_pressure_groups(steps, p.fem)
    assert set(p.fem.elsets) == before
    with pytest.raises(UnsupportedLoadType, match=taken):
        a.to_fem("d", "code_aster", scratch_dir=tmp_path, overwrite=True)
    assert set(p.fem.elsets) == before


def test_a_failure_while_adding_the_groups_takes_the_added_ones_off(monkeypatch):
    from ada.fem.formats.code_aster.write.write_loads import add_pressure_groups
    from ada.fem.formats.code_aster.write.write_steps import all_steps

    a, p = panel({"LC1": [(0, 50e3)], "LC3": [(0, 30e3), (1, 20e3)], "C13": {"LC1": 1.0, "LC3": 1.0}})
    before = set(p.fem.elsets)
    add, calls = p.fem.sets.add, []

    def failing(fs, *args, **kwargs):
        calls.append(fs.name)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return add(fs, *args, **kwargs)

    monkeypatch.setattr(p.fem.sets, "add", failing)
    with pytest.raises(RuntimeError):
        add_pressure_groups(all_steps(a), p.fem)
    assert len(calls) == 2 and set(p.fem.elsets) == before

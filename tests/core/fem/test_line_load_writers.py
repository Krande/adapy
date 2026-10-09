"""A line load (:class:`~ada.fem.LoadLine`) through the Calculix and Code_Aster writers.

Both writers raised on one (Calculix ``ValueError: Calculix does not accept Loads without reference to a fem_set``,
Code_Aster ``NotImplementedError: Load type "line"``), so a model with a line load in a step they write did not
write at all. Calculix now writes every segment as the consistent nodal forces of the linear element (``*CLOAD``);
Code_Aster writes a beam element loaded uniformly end to end as ``FORCE_POUTRE`` and the rest as ``FORCE_NODALE``,
over mesh groups of its own. The nodal forces are :meth:`LoadLine.nodal_loads`, the conversion the Abaqus writer uses.

Code_Aster is not installed here, so nothing is solved: each deck's records are summed back into a force and a moment
and compared with the load's own, and the concept load's closed form. The part: a 4 m beam at y = 1.5 under a
uniform (1000 N/m), a partial (1.3..2.9 m) and a linear (1000 -> 3000 N/m) load, and a 4 x 1 m plate with
(100, 0, -500) N/m along its y = 0 edge, which has no beam.
"""

from __future__ import annotations

import re

import h5py
import numpy as np
import pytest

import ada
from ada.fem import LoadLine
from ada.fem.concept.constraints import ConstraintConceptPoint
from ada.fem.concept.loads import LoadConceptCase, LoadConceptLine
from ada.fem.formats import conversion_report
from ada.fem.steps import StepImplicitStatic

Q = (0, 0, -1000.0)
#: The concept loads, and the force each one sums to.
CASES = {
    "LC_u": (LoadConceptLine("u", (0, 1.5, 0), (4, 1.5, 0), Q, Q), (0, 0, -4000.0)),
    "LC_part": (LoadConceptLine("part", (1.3, 1.5, 0), (2.9, 1.5, 0), Q, Q), (0, 0, -1600.0)),
    "LC_lin": (LoadConceptLine("lin", (0, 1.5, 0), (4, 1.5, 0), (0, 0, -1000.0), (0, 0, -3000.0)), (0, 0, -8000.0)),
    "LC_edge": (LoadConceptLine("edge", (0, 0, 0), (4, 0, 0), (100, 0, -500.0), (100, 0, -500.0)), (400, 0, -2000.0)),
}


@pytest.fixture(scope="module")
def meshed():
    bm = ada.Beam("bm", (0, 1.5, 0), (4, 1.5, 0), "IPE300")
    pl = ada.Plate.from_3d_points("pl", [(0, 0, 0), (4, 0, 0), (4, 1, 0), (0, 1, 0)], 0.01)
    p = ada.Part("p") / [bm, pl]
    a = ada.Assembly("a") / p
    c = p.concept_fem.constraints
    for i, pos in enumerate([(0, 1.5, 0), (4, 1.5, 0), (0, 1, 0), (4, 1, 0)]):
        c.add_point_constraint(ConstraintConceptPoint(f"fix{i}", pos, []))
    for i, (name, (load, _)) in enumerate(CASES.items(), start=1):
        p.concept_fem.loads.add_load_case(LoadConceptCase(name, [load], fem_loadcase_number=i))
    p.fem = p.to_fem_obj(0.5, "line")
    (step,) = p.fem.steps
    loads = {name: lc.loads for name, lc in step.load_cases.items()}
    assert all(len(v) == 1 and isinstance(v[0], LoadLine) for v in loads.values())
    return a, p, {name: v[0] for name, v in loads.items()}


def _write(meshed, fmt, case, tmp_path):
    """Write the part with ``case``'s line load as the one load of an assembly step -- the steps these writers
    write -- and return the deck's text, the line load and the conversion report."""
    a, p, loads = meshed
    load = LoadLine(f"ll_{case}", loads[case].segments)
    step = a.fem.add_step(StepImplicitStatic("s1"))
    step.add_load(load)
    try:
        with conversion_report.collect() as report:
            a.to_fem(case, fmt, scratch_dir=tmp_path, overwrite=True)
    finally:
        a.fem.steps.remove(step)
    suffix = ".inp" if fmt == "calculix" else ".comm"
    return (tmp_path / case / case).with_suffix(suffix).read_text(), load, report


def _summed(points_forces) -> tuple[np.ndarray, np.ndarray]:
    """The total force and its moment about the origin of ``(position, force)`` pairs."""
    f, m = np.zeros(3), np.zeros(3)
    for pos, force in points_forces:
        f += force
        m += np.cross(pos, force)
    return f, m


def _expected(load: LoadLine) -> tuple[np.ndarray, np.ndarray]:
    return load.resultant(), sum((s.moment() for s in load.segments), np.zeros(3))


# --- Calculix --------------------------------------------------------------------------------------------------------


def _ccx_cloads(inp: str, p) -> list[tuple[np.ndarray, np.ndarray]]:
    (block,) = re.findall(r"^\*Cload\n((?:\d+, \d, \S+\n)+)", inp, re.M)
    out = []
    for nid, dof, value in re.findall(r"^(\d+), (\d), (\S+)$", block, re.M):
        force = np.zeros(3)
        force[int(dof) - 1] = float(value)
        out.append((np.asarray(p.fem.nodes.from_id(int(nid)).p, dtype=float), force))
    return out


@pytest.mark.parametrize("case", list(CASES))
def test_calculix_writes_a_line_load_as_nodal_forces_summing_to_the_load(meshed, case, tmp_path):
    inp, load, report = _write(meshed, "calculix", case, tmp_path)
    _, p, _ = meshed
    assert "*Dload" not in inp.split("** LOADS")[1]
    force, moment = _summed(_ccx_cloads(inp, p))
    want_f, want_m = _expected(load)
    assert force == pytest.approx(want_f, abs=1e-6)
    assert force == pytest.approx(np.asarray(CASES[case][1]), abs=1e-6)
    assert moment == pytest.approx(want_m, abs=1e-6)
    (note,) = [f for f in report.findings if f.keyword == "LoadLine"]
    assert (note.kind, note.stage, note.subject) == ("note", "calculix writer", load.name)


def test_calculix_nodal_forces_are_the_consistent_ones(meshed, tmp_path):
    """The partial load 1.3..2.9 m on 0.5 m elements: the element 2.5..3.0 is loaded over 2.5..2.9, 400 N, which the
    linear element's shape functions share 240 to the node at 2.5 and 160 to the node at 3.0; the node at 2.5 also
    takes 250 from the element before it, which is loaded whole."""
    inp, _, _ = _write(meshed, "calculix", "LC_part", tmp_path)
    _, p, _ = meshed
    by_x = {round(float(pos[0]), 6): float(f[2]) for pos, f in _ccx_cloads(inp, p)}
    assert by_x == pytest.approx({1.0: -40.0, 1.5: -410.0, 2.0: -500.0, 2.5: -490.0, 3.0: -160.0})


# --- Code_Aster ------------------------------------------------------------------------------------------------------


def _med_groups(med_file) -> dict[str, set[int]]:
    """Every group in the MED file and the ids of its cells or nodes, read from the families, as Code_Aster does."""
    groups: dict[str, set[int]] = {}
    with h5py.File(med_file, "r") as f:
        (mesh,) = f["ENS_MAA"].values()
        (fams,) = f["FAS"].values()
        (ts,) = mesh.values()
        for kind, entities in (("ELEME", ts["MAI"].values()), ("NOEUD", [ts["NOE"]])):
            if kind not in fams:
                continue
            fam_groups = {}
            for fam in fams[kind].values():
                names = [bytes(n).decode().rstrip("\x00").strip() for n in fam["GRO"]["NOM"][()]]
                fam_groups[int(fam.attrs["NUM"])] = names
            for ent in entities:
                ids = ent["NUM"][()] if "NUM" in ent else np.arange(1, len(ent["FAM"][()]) + 1)
                for i, fam_num in zip(ids, ent["FAM"][()]):
                    for name in fam_groups.get(int(fam_num), []):
                        groups.setdefault(name, set()).add(int(i))
    return groups


def _aster_records(comm: str, load: LoadLine, p, groups) -> tuple[list, list]:
    """The load's ``FORCE_POUTRE`` rows as (element, q) and ``FORCE_NODALE`` rows as (node position, force)."""
    m = re.search(rf"^{load.name} = AFFE_CHAR_MECA\(\n(.*?)^\)$", comm, re.M | re.S)
    assert m, f"no AFFE_CHAR_MECA for {load.name}"
    body = m[1]
    num = r"(-?[\d.eE+-]+)"
    beam = [
        (p.fem.elements.from_id(el), np.array([float(fx), float(fy), float(fz)]))
        for name, fx, fy, fz in re.findall(rf"_F\(GROUP_MA='(\w+)', FX={num}, FY={num}, FZ={num}\)", body)
        for el in sorted(groups[name])
    ]
    nodal = [
        (np.asarray(p.fem.nodes.from_id(n).p, dtype=float), np.array([float(fx), float(fy), float(fz)]))
        for name, fx, fy, fz in re.findall(rf"_F\(GROUP_NO='(\w+)', FX={num}, FY={num}, FZ={num}\)", body)
        for n in sorted(groups[name])
    ]
    return beam, nodal


@pytest.mark.parametrize("case", list(CASES))
def test_code_aster_writes_a_line_load_summing_to_the_load(meshed, case, tmp_path):
    comm, load, report = _write(meshed, "code_aster", case, tmp_path)
    _, p, _ = meshed
    groups = _med_groups(tmp_path / case / f"{case}.med")
    beam, nodal = _aster_records(comm, load, p, groups)
    pairs = list(nodal)
    for el, q in beam:
        a, b = (np.asarray(n.p, dtype=float) for n in el.nodes[:2])
        pairs.append(((a + b) / 2, q * np.linalg.norm(b - a)))
    force, moment = _summed(pairs)
    want_f, want_m = _expected(load)
    assert force == pytest.approx(want_f, abs=1e-6)
    assert force == pytest.approx(np.asarray(CASES[case][1]), abs=1e-6)
    assert moment == pytest.approx(want_m, abs=1e-6)
    # FORCE_POUTRE exactly where a beam element is loaded uniformly end to end, nodal forces everywhere else
    whole = {s.elem.id for s in load.segments if s.uniform_over_beam_element()}
    assert {el.id for el, _ in beam} == whole
    assert (len(nodal) > 0) == (len(whole) < len(load.segments))
    notes = [f for f in report.findings if f.keyword == "LoadLine"]
    if nodal:
        (note,) = notes
        assert (note.kind, note.stage, note.subject) == ("note", "code_aster writer", load.name)
    else:
        assert notes == []


def test_code_aster_writes_each_kind_where_it_is_exact(meshed, tmp_path):
    """Uniform over the whole beam: FORCE_POUTRE only, one group; linear: FORCE_NODALE only; partial: both."""
    for case, kinds in {
        "LC_u": {"FORCE_POUTRE"},
        "LC_lin": {"FORCE_NODALE"},
        "LC_part": {"FORCE_POUTRE", "FORCE_NODALE"},
        "LC_edge": {"FORCE_NODALE"},
    }.items():
        comm, load, _ = _write(meshed, "code_aster", case, tmp_path / case)
        body = comm.split(f"{load.name} = AFFE_CHAR_MECA(")[1].split("\n)\n")[0]
        assert {k for k in ("FORCE_POUTRE", "FORCE_NODALE") if k in body} == kinds
        assert "EXCIT=(" in comm and f"_F(CHARGE={load.name})" in comm


def test_code_aster_takes_its_line_load_groups_off_the_model_again(meshed, tmp_path):
    _, p, _ = meshed
    before = (sorted(p.fem.elsets), sorted(p.fem.nsets))
    _write(meshed, "code_aster", "LC_part", tmp_path)
    assert (sorted(p.fem.elsets), sorted(p.fem.nsets)) == before

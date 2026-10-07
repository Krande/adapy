"""Concept loads meshed by adapy into the load records GeniE meshes them into, case by case and node by node.

The GeniE fixture ``files/fem_files/sesam/genie_loads_all_kinds.xml`` (GeniE V9.2-01, one load case per kind: point
loads on a beam and a plate, uniform, linear, partial and line-footprint line loads on beams, a line load along a
plate edge, front and back pressure, three pressures the reader refuses, gravity and the combination LCC1) is read
with ``from_genie_xml``, meshed by adapy at GeniE's 0.5 m and written as a Sesam deck. Its load records are compared
with the ``T1.FEM`` GeniE meshed the same model into (``genie_loads_all_kinds_T1.FEM``), keyed by geometry so that
element and node numbers do not matter:

* BNLOAD by node position; BELOAD1 by its element's end positions (L1, L2, the six intensities, LOTYP, OPT);
  BELLO2 by its edge's end positions (the intensities in that order -- adapy's plate elements are wound the other
  way round from GeniE's, so an edge's two nodes come in the other order); BEUSLO by element centroid, as the force
  per area it pushes with (sign of RLOAD times the element's own normal); BGRAV by its acceleration.

Before this conversion existed the deck carried none of these records, and nothing said so.
"""

from __future__ import annotations

import collections
import pathlib
import shutil

import numpy as np
import pytest

import ada
from ada.fem.formats import conversion_report

STAGE = "concept to fem"
FILES = pathlib.Path(__file__).resolve().parents[3] / "files" / "fem_files" / "sesam"

#: The load cases whose loads the GeniE XML reader refuses (each reported there): a varying pressure (two kinds), a
#: component traction, a polygon pressure. adapy writes them as empty load cases.
REFUSED_BY_READER = {"LC_press_3pt", "LC_press_lin", "LC_comp", "LC_poly"}

#: GeniE's own resultant per load case (summed force, moment of the BNLOADs about their node), read off
#: ``genie_loads_all_kinds_T1.FEM``. adapy's deck must give the same.
GENIE_RESULTANTS = {
    "LC_pt_beam": ((0, 0, -10000), (0, 500, 0)),
    "LC_pt_beampoint": ((0, 0, -2000), (0, 0, 0)),
    "LC_pt_plate": ((0, 0, -5000), (0, 0, 0)),
    "LC_ll_beam_u": ((0, 0, -4000), (0, 0, 0)),
    "LC_ll_beam_v": ((0, 0, -8000), (0, 0, 0)),
    "LC_ll_beam_part": ((0, 0, -4000), (0, 0, 0)),
    "LC_ll_beam_local": ((0, 0, -4000), (0, 0, 0)),
    "LC_ll_line": ((0, 0, -6000), (0, 0, 0)),
    "LC_ll_plate_edge": ((0, 0, -500), (0, 0, 0)),
    "LC_press_u": ((0, 0, -4000), (0, 0, 0)),
    "LC_press_back": ((0, 0, 4000), (0, 0, 0)),
    "LC_grav": ((0, 0, 0), (0, 0, 0)),
}


def _key(p) -> tuple:
    return tuple(round(float(c), 3) + 0.0 for c in p)


def _round(values, digits=3) -> tuple:
    return tuple(round(float(v), digits) + 0.0 for v in values)


def _records(fem_path) -> list[tuple[str, list]]:
    """Free-format Sesam records: an 8-character name, then numbers; continuation lines start blank. A TDLOAD's
    name line is kept as text."""
    recs = []
    for line in open(fem_path):
        if not line.strip():
            continue
        if line[0] != " ":
            recs.append([line[:8].strip(), []])
        elif recs[-1][0] == "TDLOAD":
            recs[-1][1].append(line.strip())
            continue
        for tok in line[8:].split():
            try:
                recs[-1][1].append(float(tok))
            except ValueError:
                recs[-1][1].append(tok)
    return recs


def _shell_normal(xs) -> np.ndarray:
    if len(xs) == 4:
        return 0.5 * np.cross(xs[2] - xs[0], xs[3] - xs[1])
    return 0.5 * np.cross(xs[1] - xs[0], xs[2] - xs[0])


def load_records(fem_path) -> dict[str, dict[tuple, tuple]]:
    """``{load case name: {geometric key: values}}`` for every load record of a deck (see the module docstring)."""
    recs = _records(fem_path)
    coord, elnodes, names = {}, {}, {}
    for name, v in recs:
        if name == "GCOORD":
            coord[int(v[0])] = np.asarray(v[1:4], dtype=float)
        elif name == "GELMNT1":
            elnodes[int(v[0])] = [int(x) for x in v[4:]]
        elif name == "TDLOAD":
            names[int(v[1])] = v[-1]
    out = collections.defaultdict(dict)
    for name, v in recs:
        if name == "BNLOAD":
            out[names[int(v[0])]][("BNLOAD", _key(coord[int(v[4])]))] = _round(v[6 : 6 + int(v[5])])
        elif name == "BELOAD1":
            ns = elnodes[int(v[4])]
            key = ("BELOAD1", _key(coord[ns[0]]), _key(coord[ns[-1]]))
            out[names[int(v[0])]][key] = (round(v[5], 4), round(v[6], 4), _round(v[9:15]), int(v[1]), int(v[3]))
        elif name == "BELLO2":
            ns, line = elnodes[int(v[4])], int(v[7])
            a, b = _key(coord[ns[line - 1]]), _key(coord[ns[line % len(ns)]])
            q = _round(v[9:15])
            if b < a:
                a, b, q = b, a, q[3:] + q[:3]
            out[names[int(v[0])]][("BELLO2", a, b)] = q
        elif name == "BEUSLO":
            xs = [coord[k] for k in elnodes[int(v[4])]]
            normal = _shell_normal(xs)
            push = np.mean(v[8 : 8 + int(v[5])]) * normal / np.linalg.norm(normal)
            out[names[int(v[0])]][("BEUSLO", _key(np.mean(xs, axis=0)))] = (_round(push), int(v[1]))
        elif name == "BGRAV":
            out[names[int(v[0])]][("BGRAV",)] = _round(v[4:7], 5)
    for name in names.values():
        out.setdefault(name, {})
    return dict(out)


def resultants(fem_path) -> dict[str, tuple[tuple, tuple]]:
    """``{load case: (summed force, summed applied moment)}`` -- line and surface loads integrated exactly for the
    linear intensities they hold."""
    recs = _records(fem_path)
    coord, elnodes, names = {}, {}, {}
    for name, v in recs:
        if name == "GCOORD":
            coord[int(v[0])] = np.asarray(v[1:4], dtype=float)
        elif name == "GELMNT1":
            elnodes[int(v[0])] = [int(x) for x in v[4:]]
        elif name == "TDLOAD":
            names[int(v[1])] = v[-1]
    force = collections.defaultdict(lambda: np.zeros(3))
    moment = collections.defaultdict(lambda: np.zeros(3))
    for name, v in recs:
        if name == "BNLOAD":
            comps = np.asarray(v[6 : 6 + int(v[5])], dtype=float)
            force[int(v[0])] += comps[:3]
            moment[int(v[0])] += comps[3:6] if len(comps) >= 6 else 0.0
        elif name in ("BELOAD1", "BELLO2"):
            ns = elnodes[int(v[4])]
            if name == "BELOAD1":
                a, b = coord[ns[0]], coord[ns[-1]]
                length = np.linalg.norm(b - a) - v[5] - v[6]
            else:
                line = int(v[7])
                a, b = coord[ns[line - 1]], coord[ns[line % len(ns)]]
                length = np.linalg.norm(b - a)
            q = np.asarray(v[9:15], dtype=float)
            force[int(v[0])] += 0.5 * (q[:3] + q[3:]) * length
        elif name == "BEUSLO":
            xs = [coord[k] for k in elnodes[int(v[4])]]
            force[int(v[0])] += np.mean(v[8 : 8 + int(v[5])]) * _shell_normal(xs)
    return {names[k]: (_round(force[k]), _round(moment[k])) for k in names}


def mesh_and_write(xml: pathlib.Path, work: pathlib.Path, **mesh_kw):
    shutil.copy(xml, work / xml.name)
    with conversion_report.collect() as report:
        a = ada.from_genie_xml(work / xml.name)
        (part,) = a.get_all_subparts()
        part.fem = part.to_fem_obj(0.5, use_quads=True, **mesh_kw)
        a.to_fem("adapy", "sesam", scratch_dir=work, overwrite=True)
    return a, part, next((work / "adapy").glob("*T1.FEM")), report


@pytest.fixture(scope="module")
def all_kinds(tmp_path_factory):
    xml = FILES / "genie_loads_all_kinds.xml"
    a, part, deck, report = mesh_and_write(xml, tmp_path_factory.mktemp("loads_all_kinds"))
    return a, part, deck, report


def test_each_load_case_is_genies_records_node_by_node(all_kinds):
    _, _, deck, _ = all_kinds
    genie, adapy = load_records(FILES / "genie_loads_all_kinds_T1.FEM"), load_records(deck)
    assert set(genie) - REFUSED_BY_READER == set(GENIE_RESULTANTS), "the oracle's load cases"
    for case in sorted(set(genie) - REFUSED_BY_READER):
        assert len(genie[case]) > 0, case
        assert adapy[case] == genie[case], case
    for case in sorted(REFUSED_BY_READER):
        assert len(genie[case]) > 0 and adapy[case] == {}, case


def test_each_load_case_has_genies_resultant(all_kinds):
    _, _, deck, _ = all_kinds
    genie, adapy = resultants(FILES / "genie_loads_all_kinds_T1.FEM"), resultants(deck)
    for case, (f, m) in GENIE_RESULTANTS.items():
        want = (_round(f), _round(m))
        assert genie[case] == want, f"the oracle, {case}"
        assert adapy[case] == want, case


def test_the_record_kinds_compared_are_all_of_genies(all_kinds):
    """Not vacuous: every kind of record GeniE wrote for a converted case is among those compared equal."""
    _, _, deck, _ = all_kinds
    genie, adapy = load_records(FILES / "genie_loads_all_kinds_T1.FEM"), load_records(deck)
    same = {k[0] for case in GENIE_RESULTANTS for k, v in genie[case].items() if adapy[case].get(k) == v}
    assert same == {"BNLOAD", "BELOAD1", "BELLO2", "BEUSLO", "BGRAV"}


def test_the_load_cases_are_one_static_step_in_genies_order_with_a_sestra_inp(all_kinds):
    """A GeniE model meshed by adapy used to have no FE step, so its Sesam deck came with no sestra.inp and Sestra
    could not run it."""
    _, part, deck, _ = all_kinds
    (step,) = part.fem.steps
    names = list(step.load_cases)
    assert names[:16] == [lc.name for lc in sorted(part.concept_fem.loads.load_cases.values(), key=_number)]
    assert names[16:] == ["LCC1"]
    assert (deck.parent / "sestra.inp").exists()
    tdload = [v[-1] for name, v in _records(deck) if name == "TDLOAD"]
    assert tdload == names


def _number(lc):
    return lc.fem_loadcase_number


def test_a_combination_is_its_cases_times_their_factors(all_kinds):
    """LCC1 = 1.5 LC_ll_beam_u + 1.0 LC_grav. GeniE writes no record for a combination; adapy writes it as a case."""
    _, _, deck, report = all_kinds
    adapy = load_records(deck)
    beam = {k: (v[0], v[1], _round(1.5 * np.asarray(v[2])), v[3], v[4]) for k, v in adapy["LC_ll_beam_u"].items()}
    assert adapy["LCC1"] == {**beam, **adapy["LC_grav"]}
    assert resultants(deck)["LCC1"][0] == (0.0, 0.0, -6000.0)
    (note,) = [f for f in report.findings if f.stage == STAGE and f.subject == "LCC1"]
    assert note.kind == "note" and note.details["factors"] == {"LC_ll_beam_u": 1.5, "LC_grav": 1.0}


# --- partial and reversed line loads, against GeniE's records on GeniE's own mesh -----------------------------------


def _beam_part(name="p", *loads, cases=None):
    bm = ada.Beam("bm", (0, 1.5, 0), (4, 1.5, 0), "IPE300")
    p = ada.Part(name) / bm
    a = ada.Assembly(f"{name}_a") / p
    from ada.fem.concept.loads import LoadConceptCase

    for lc_name, lc_loads in (cases or {"LC": list(loads)}).items():
        p.concept_fem.loads.add_load_case(LoadConceptCase(lc_name, list(lc_loads)))
    return a, p


def _write(a, p, tmp_path, **mesh_kw):
    with conversion_report.collect() as report:
        p.fem = p.to_fem_obj(0.5, "line", **mesh_kw)
        a.to_fem("d", "sesam", scratch_dir=tmp_path, overwrite=True)
    return next((tmp_path / "d").glob("*T1.FEM")), report


def test_a_partial_line_load_has_genies_end_lengths(tmp_path):
    """GeniE's LC4 of the reparam fixture: 1000 N/m from x = 1.3 to 2.9 on a beam meshed at 0.5 m. GeniE wrote L1 =
    0.3 on the element 1.0..1.5 and L2 = 0.1 on 2.5..3.0, and whole elements between."""
    from ada.fem.concept.loads import LoadConceptLine

    a, p = _beam_part("part", LoadConceptLine("LL2", (1.3, 1.5, 0), (2.9, 1.5, 0), (0, 0, -1000), (0, 0, -1000)))
    deck, _ = _write(a, p, tmp_path)
    genie = load_records(FILES / "genie_loads_reparam_T1.FEM")["LC4"]
    assert genie[("BELOAD1", (1.0, 1.5, 0.0), (1.5, 1.5, 0.0))][:2] == (0.3, 0.0)
    assert genie[("BELOAD1", (2.5, 1.5, 0.0), (3.0, 1.5, 0.0))][:2] == (0.0, 0.1)
    assert load_records(deck)["LC"] == genie
    assert resultants(deck)["LC"][0] == (0.0, 0.0, -1600.0)


def test_a_line_load_given_end_to_start_is_the_same_load(tmp_path):
    """LC_ll_beam_v runs 1000 -> 3000 N/m from x = 0 to 4; given from x = 4 to 0 with 3000 -> 1000 it is the same
    load, and its per-element end values are GeniE's."""
    from ada.fem.concept.loads import LoadConceptLine

    a, p = _beam_part("rev", LoadConceptLine("LL", (4, 1.5, 0), (0, 1.5, 0), (0, 0, -3000), (0, 0, -1000)))
    deck, _ = _write(a, p, tmp_path)
    genie = load_records(FILES / "genie_loads_all_kinds_T1.FEM")["LC_ll_beam_v"]
    assert load_records(deck)["LC"] == genie
    assert genie[("BELOAD1", (0.0, 1.5, 0.0), (0.5, 1.5, 0.0))][2] == (0.0, 0.0, -1000.0, 0.0, 0.0, -1250.0)


# --- what is not converted is a finding ------------------------------------------------------------------------------


def _found(report) -> dict[str, list[tuple[str, str]]]:
    out = collections.defaultdict(list)
    for f in report.findings:
        if f.stage == STAGE:
            for subject in [f.subject, *f.other_subjects]:
                out[subject].append((f.kind, f.keyword))
    return dict(out)


def test_every_load_that_becomes_no_fe_load_is_named(tmp_path):
    from ada.fem.concept.loads import (
        LoadConceptAccelerationField,
        LoadConceptLine,
        LoadConceptPoint,
        LoadConceptSurface,
        RotationalAccelerationField,
    )

    rot = RotationalAccelerationField((0, 0, 0), (0, 0, 1), 1.0, 0.0)
    cases = {
        "LC_poly": [LoadConceptSurface("SP", points=[(0, 0, 0), (1, 0, 0), (1, 1, 0)], pressure=1000.0)],
        "LC_rot": [LoadConceptAccelerationField("G", (0, 0, -9.81), rotational_field=rot)],
        "LC_acc": [LoadConceptAccelerationField("A", (1.0, 0, 0), include_self_weight=False)],
        "LC_off": [LoadConceptPoint("P", (2, 5, 0), (0, 0, -1), (0, 0, 0))],
        "LC_nothing": [LoadConceptLine("L0", (0, 3, 0), (4, 3, 0), (0, 0, -1), (0, 0, -1))],
        "LC_partly": [LoadConceptLine("L1", (3, 1.5, 0), (5, 1.5, 0), (0, 0, -1), (0, 0, -1))],
    }
    a, p = _beam_part("named", cases=cases)
    deck, report = _write(a, p, tmp_path)
    found = _found(report)
    assert found["SP in load case LC_poly"] == [("omitted", "LoadConceptSurface")]
    assert found["G in load case LC_rot"] == [("omitted", "RotationalAccelerationField")]
    assert found["A in load case LC_acc"] == [("omitted", "LoadConceptAccelerationField")]
    assert found["P in load case LC_off"] == [("omitted", "LoadConceptPoint")]
    assert found["L0 in load case LC_nothing"] == [("omitted", "LoadConceptLine")]
    (partly,) = [f for f in report.findings if f.subject == "L1 in load case LC_partly"]
    assert partly.kind == "omitted" and partly.details["not_covered"] == pytest.approx(1.0)
    # what is written: the gravity of LC_rot and the part of L1 on the beam
    res = resultants(deck)
    assert res["LC_partly"][0] == (0.0, 0.0, -1.0)
    assert load_records(deck)["LC_rot"] == {("BGRAV",): (0.0, 0.0, -9.81)}


def test_concept_loads_a_writer_gets_unconverted_are_named(tmp_path):
    """A part whose FEM was not meshed from its concept loads (here: loads added after meshing) writes none of them;
    that used to be silent."""
    from ada.fem.concept.loads import LoadConceptCase, LoadConceptPoint

    a, p = _beam_part("late", LoadConceptPoint("P", (2, 1.5, 0), (0, 0, -1), (0, 0, 0)))
    p.fem = p.to_fem_obj(0.5, "line")
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("LC_late", [LoadConceptPoint("Q", (1, 1.5, 0), (0, 0, -1), (0, 0, 0))])
    )
    with conversion_report.collect() as report:
        a.to_fem("late", "sesam", scratch_dir=tmp_path, overwrite=True)
    found = _found(report)
    assert found["LC_late of part late"] == [("omitted", "LoadConceptCase")]
    assert "LC of part late" not in found


# --- a node at every load and support point ------------------------------------------------------------------------


def test_a_point_load_in_a_span_gets_a_node_of_its_own(tmp_path):
    """x = 1.3 on a 4 m beam meshed at 0.5 m has no node: without embedding the load acts on nothing (reported), with
    it the mesh has one more node, exactly there, and the load is a BNLOAD on it."""
    from ada.fem.concept.loads import LoadConceptPoint

    def run(embed):
        a, p = _beam_part(f"pt{embed}", LoadConceptPoint("P", (1.3, 1.5, 0), (0, 0, -10000), (0, 0, 0)))
        deck, report = _write(a, p, tmp_path / str(embed), embed_concept_points=embed)
        return p.fem, deck, report

    (tmp_path / "True").mkdir(), (tmp_path / "False").mkdir()
    fem_off, deck_off, rep_off = run(False)
    fem_on, deck_on, rep_on = run(True)
    assert len(fem_on.nodes) == len(fem_off.nodes) + 1
    assert len(fem_on.elements) == len(fem_off.elements) + 1
    assert _found(rep_off)["P in load case LC"] == [("omitted", "LoadConceptPoint")]
    assert "P in load case LC" not in _found(rep_on)
    assert load_records(deck_on)["LC"] == {("BNLOAD", (1.3, 1.5, 0.0)): (0.0, 0.0, -10000.0, 0.0, 0.0, 0.0)}


def _plate_part(name, *loads):
    from ada.fem.concept.loads import LoadConceptCase

    pl = ada.Plate.from_3d_points("pl", [(0, 0, 0), (4, 0, 0), (4, 1, 0), (0, 1, 0)], 0.01)
    p = ada.Part(name) / pl
    a = ada.Assembly(f"{name}_a") / p
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC", list(loads)))
    return a, p, pl


@pytest.mark.parametrize("point", [(2.3, 0.4, 0.0), (4.0, 0.3, 0.0)], ids=["inside", "on_edge"])
def test_a_point_on_a_plate_gets_a_node_in_a_triangle_mesh(tmp_path, point):
    """Measured: the triangle mesh of the plate keeps 36 nodes with the inside point embedded (the triangulation is
    regenerated around it) and gains one with the point on the edge; either way the point is a node and the mesh is
    one of triangles that all have area."""
    from ada.fem.concept.loads import LoadConceptPoint

    a, p, _ = _plate_part("plpt", LoadConceptPoint("P", point, (0, 0, -1), (0, 0, 0)))
    with conversion_report.collect() as report:
        fem = p.to_fem_obj(0.5)
    assert [n for n in fem.nodes if np.linalg.norm(np.subtract(n.p, point)) < 1e-9]
    assert STAGE not in {f.stage for f in report.findings}
    areas = [np.linalg.norm(_shell_normal([np.asarray(n.p) for n in el.nodes])) for el in fem.elements]
    assert min(areas) > 1e-4
    from ada.fem.conformality import check_conformal_mesh

    assert check_conformal_mesh(fem) == []


def test_a_point_on_a_plate_in_a_quad_mesh_is_said_to_have_no_node(tmp_path):
    """A quad mesh is transfinite per plate and takes no extra vertex: a point on the plate's edge stopped gmsh with
    "Surface 1 is transfinite but has 5 corners" when embedded. It is left out, said so, and the load is reported."""
    from ada.fem.concept.loads import LoadConceptPoint

    a, p, _ = _plate_part("plq", LoadConceptPoint("P", (4.0, 0.3, 0.0), (0, 0, -1), (0, 0, 0)))
    with conversion_report.collect() as report:
        p.to_fem_obj(0.5, use_quads=True)
    found = _found(report)
    assert found["(4.0, 0.3, 0.0)"] == [("note", "Point")]
    assert found["P in load case LC"] == [("omitted", "LoadConceptPoint")]


# --- pressure side against the plate's own normal ------------------------------------------------------------------


@pytest.mark.parametrize("side, fz", [("front", -4000.0), ("back", 4000.0)])
def test_a_front_pressure_pushes_into_the_plates_normal_side_whatever_the_element_winding(tmp_path, side, fz):
    """GeniE: 1000 Pa on the front of a plate whose normal is +z gave Fz = -4000 on its 4 m2. Here the plate's normal
    is +z and gmsh winds its elements -z (measured), so the front is every element's negative face."""
    from ada.fem.concept.loads import LoadConceptSurface

    pl = ada.Plate.from_3d_points("pl", [(0, 0, 0), (0, 1, 0), (4, 1, 0), (4, 0, 0)], 0.01)
    a = ada.Assembly("pa") / (ada.Part("pp") / pl)
    (p,) = a.get_all_subparts()
    from ada.fem.concept.loads import LoadConceptCase

    p.concept_fem.loads.add_load_case(LoadConceptCase("LC", [LoadConceptSurface("S", pl, pressure=1000.0, side=side)]))
    assert np.allclose(pl.poly.normal, (0, 0, -1)) or np.allclose(pl.poly.normal, (0, 0, 1))
    deck, _ = _write(a, p, tmp_path)
    normal_z = float(np.sign(pl.poly.normal[2]))
    assert resultants(deck)["LC"][0] == (0.0, 0.0, normal_z * fz)


def test_the_steps_of_several_meshed_parts_reach_the_writer_which_names_what_it_cannot_hold(tmp_path):
    """A single-part writer gets the parts merged into one, and the merge keeps each part's concept-load step (on the
    merged part's FEM, re-keyed). Sesam holds one load set per file: it writes the first and names the second, with
    its loads -- nothing is dropped by the merge without a word."""
    from ada.fem.concept.loads import LoadConceptCase, LoadConceptPoint

    parts = []
    for i in (1, 2):
        bm = ada.Beam(f"bm{i}", (0, 3 * i, 0), (4, 3 * i, 0), "IPE300")
        p = ada.Part(f"p{i}") / bm
        p.concept_fem.loads.add_load_case(
            LoadConceptCase(f"LC{i}", [LoadConceptPoint("P", (2, 3 * i, 0), (0, 0, -1), (0, 0, 0))])
        )
        parts.append(p)
    a = ada.Assembly("two") / parts
    for p in parts:
        p.fem = p.to_fem_obj(0.5, "line")
    with conversion_report.collect() as report:
        a.to_fem("two", "sesam", scratch_dir=tmp_path, overwrite=True)
    steps = [f for f in report.findings if f.keyword == "Step" and f.subject == "concept_loads"]
    assert not [f for f in steps if f.stage != "sesam writer"]  # the merge loses no step
    (unwritten,) = [f for f in steps if f.kind == "omitted" and "only the first step is written" in f.reason]
    assert unwritten.details == {"n_loads": 1, "n_bcs": 0}


@pytest.mark.parametrize("fmt", ["usfos"])
def test_a_writer_of_the_assembly_steps_only_names_the_part_step_it_leaves_out(tmp_path, fmt):
    """Usfos writes no step: the step a part's concept load cases became (on the part's FEM) and its loads were left
    out of a one-part model without a word. (Calculix and Code_Aster write it now --
    ``test_calculix_and_code_aster_write_the_part_step``.)"""
    from ada.fem.concept.constraints import ConstraintConceptPoint
    from ada.fem.concept.loads import LoadConceptLine

    q = (0, 0, -1000.0)
    a, p = _beam_part("p", LoadConceptLine("u", (0, 1.5, 0), (4, 1.5, 0), q, q))
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("fix", (0, 1.5, 0), []))
    p.fem = p.to_fem_obj(0.5, "line")
    with conversion_report.collect() as report:
        a.to_fem("one", fmt, scratch_dir=tmp_path, overwrite=True)
    (lost,) = [f for f in report.findings if f.keyword == "Step" and f.subject == "concept_loads"]
    assert (lost.kind, lost.stage, lost.details) == ("omitted", f"{fmt} writer", {"part": "p", "n_loads": 1})


@pytest.mark.parametrize("fmt", ["calculix", "code_aster"])
def test_calculix_and_code_aster_write_the_part_step(tmp_path, fmt):
    """The step a part's concept load cases became is written, its load case with it, and no step is reported left
    out. Both writers wrote the assembly's steps only (Calculix its first only), so a meshed GeniE model's loads never
    reached either deck."""
    from ada.fem.concept.constraints import ConstraintConceptPoint
    from ada.fem.concept.loads import LoadConceptLine

    q = (0, 0, -1000.0)
    a, p = _beam_part("p", LoadConceptLine("u", (0, 1.5, 0), (4, 1.5, 0), q, q))
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("fix", (0, 1.5, 0), []))
    p.fem = p.to_fem_obj(0.5, "line")
    with conversion_report.collect() as report:
        a.to_fem("one", fmt, scratch_dir=tmp_path, overwrite=True)
    assert [f for f in report.findings if f.keyword == "Step"] == []
    if fmt == "calculix":
        deck = (tmp_path / "one" / "one.inp").read_text()
        assert "** STEP: concept_loads  LOAD CASE: LC" in deck
    else:
        deck = (tmp_path / "one" / "one.comm").read_text()
        assert "MACRO_ELAS_MULT(" in deck and "NOM_CAS='LC'" in deck


# --- refusals and physics the review of #435 found no test for (each fails under the mutation named) -------------


def _bello2(deck) -> list[tuple]:
    """Every BELLO2 as (load case, edge end a, edge end b), the ends in sorted order."""
    return sorted(
        (case, *sorted(ends)) for case, recs in load_records(deck).items() for (kind, *ends) in recs if kind == "BELLO2"
    )


def test_a_plate_edge_load_ending_inside_an_edge_writes_only_the_whole_edges(tmp_path):
    """1000 N/m along the plate's edge y = 0 from x = 0.3 to 3.7 on a 0.5 m quad mesh. A BELLO2 loads a whole edge, so
    the six edges 0.5..3.5 are written (3000 N) and the two it ends inside are reported by the conversion, which
    leaves them out of the FE load. Fails if ``_edge_segments`` keeps the partial edges: the FE load then holds 8
    segments, two of them part of an edge, which the Sesam writer refuses on its own (so the deck alone does not
    tell) and a writer that loads whole edges would stretch over them (8 edges, 4000 N)."""
    from ada.fem.concept.loads import LoadConceptLine

    q = (0, 0, -1000.0)
    a, p, _ = _plate_part("edge", LoadConceptLine("LE", (0.3, 0, 0), (3.7, 0, 0), q, q))
    with conversion_report.collect() as report:
        p.fem = p.to_fem_obj(0.5, use_quads=True)
        a.to_fem("d", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = next((tmp_path / "d").glob("*T1.FEM"))
    edges = _bello2(deck)
    assert [(lo[0], hi[0]) for _, lo, hi in edges] == [(0.5 * i, 0.5 * i + 0.5) for i in range(1, 7)]
    assert all(lo[1] == hi[1] == 0.0 for _, lo, hi in edges)
    assert resultants(deck)["LC"][0] == (0.0, 0.0, -3000.0)
    (inside,) = [f for f in report.findings if f.subject == "LE in load case LC" and "edges" in f.details]
    assert inside.kind == "omitted" and len(inside.details["edges"]) == 2
    (load,) = p.fem.steps[0].load_cases["LC"].loads
    assert len(load.segments) == 6 and all(s.l1 == s.l2 == 0.0 for s in load.segments)
    assert not [f for f in report.findings if f.keyword == "Load" and f.kind == "omitted"]


def test_a_line_load_along_an_interior_plate_edge_is_written_once_per_edge(tmp_path):
    """1000 N/m along y = 0.5, inside the 4 x 1 plate: each of the 8 element edges under it is shared by two quads and
    takes one BELLO2, on the lower element id of the two, 4000 N in all. Fails without the node-pair dedup: keyed per
    element edge, 16 records and 8000 N; the ``pair in by_pair`` check alone removed, the higher element ids."""
    from ada.fem.concept.loads import LoadConceptLine

    q = (0, 0, -1000.0)
    a, p, _ = _plate_part("inner", LoadConceptLine("LI", (0, 0.5, 0), (4, 0.5, 0), q, q))
    with conversion_report.collect() as report:
        p.fem = p.to_fem_obj(0.5, use_quads=True)
        a.to_fem("d", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = next((tmp_path / "d").glob("*T1.FEM"))
    edges = _bello2(deck)
    assert [(lo[0], hi[0], lo[1], hi[1]) for _, lo, hi in edges] == [
        (0.5 * i, 0.5 * i + 0.5, 0.5, 0.5) for i in range(8)
    ]
    assert resultants(deck)["LC"][0] == (0.0, 0.0, -4000.0)
    assert "LI in load case LC" not in _found(report)
    for v in [v for name, v in _records(deck) if name == "BELLO2"]:
        el = p.fem.elements.from_id(int(v[4]))
        n = len(el.nodes)
        ends = {el.nodes[int(v[7]) - 1].id, el.nodes[int(v[7]) % n].id}
        sharing = [e.id for e in p.fem.elements if ends <= {m.id for m in e.nodes}]
        assert len(sharing) == 2 and el.id == min(sharing)


def test_a_settlement_value_on_a_dof_its_support_does_not_prescribe_is_reported(tmp_path):
    """The support prescribes dz only; the case's prescribed displacement also gives dx 0.002. Sestra ignores a BNDISPL
    value on a dof without FIX code 2 (measured by the reader's authors), so the dx value has nothing to act on: it is
    named, and the case's Bc holds dz alone. Fails without the report."""
    from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
    from ada.fem.concept.constraints import ConstraintConceptPoint
    from ada.fem.concept.loads import LoadConceptCase, LoadConceptPrescribedDisplacement

    a, p = _beam_part("pdx")
    c = p.concept_fem.constraints
    c.add_point_constraint(ConstraintConceptPoint("root", (0, 1.5, 0), Dof.encastre()))
    kinds = {"dz": "prescribed"}
    sp = c.add_point_constraint(
        ConstraintConceptPoint(
            "tip", (4, 1.5, 0), [Dof(d, kinds.get(d, "free")) for d in ("dx", "dy", "dz", "rx", "ry", "rz")]
        )
    )
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("LC_pd", [LoadConceptPrescribedDisplacement("PD", sp, (0.002, 0.0, -0.01))])
    )
    with conversion_report.collect() as report:
        p.fem = p.to_fem_obj(0.5, "line")
    (bc,) = [b for b in p.fem.bcs if b.name == "tip_LC_pd"]
    assert (bc.dofs, bc.magnitudes) == ([3], [-0.01])
    (f,) = [f for f in report.findings if f.subject == "PD in load case LC_pd"]
    assert (f.kind, f.keyword, f.details) == (
        "omitted",
        "LoadConceptPrescribedDisplacement",
        {"dofs": [1], "values": [0.002]},
    )


def test_a_moment_on_a_node_of_solid_elements_only_is_dropped_and_named_and_the_force_kept(tmp_path):
    """A point load with a moment at a corner of a meshed solid box: a solid node has no rotation, so the moment is
    reported and left out and the force is written. Fails if the moment is kept."""
    from ada.fem import Load
    from ada.fem.concept.loads import LoadConceptCase, LoadConceptPoint

    box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1))
    p = ada.Part("solid") / box
    ada.Assembly("solid_a") / p
    pt = LoadConceptPoint("P", (1, 1, 1), (0, 0, -1000.0), (10.0, 0, 0))
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC", [pt]))
    with conversion_report.collect() as report:
        fem = p.to_fem_obj(0.5)
    (step,) = fem.steps
    (load,) = step.load_cases["LC"].loads
    assert load.type == Load.TYPES.FORCE and list(load.dof) == [0.0, 0.0, -1000.0, 0.0, 0.0, 0.0]
    (f,) = [f for f in report.findings if f.subject == "P in load case LC"]
    assert (f.kind, f.details["moment"]) == ("omitted", [10.0, 0.0, 0.0])


def test_a_combination_with_a_phase_angle_is_refused_not_written_as_static(tmp_path):
    """A term with a phase combines complex (harmonic) cases; a static FE load case has no phase, so the combination is
    named and no case is made of it. Fails, with LCC written as 1.0 x LC, without the phase check."""
    from ada.fem.concept.loads import (
        LoadConceptCaseCombination,
        LoadConceptCaseFactored,
        LoadConceptPoint,
    )

    a, p = _beam_part("phase", LoadConceptPoint("P", (2, 1.5, 0), (0, 0, -1000.0), (0, 0, 0)))
    (lc,) = p.concept_fem.loads.load_cases.values()
    term = LoadConceptCaseFactored(lc, 1.0, phase=90)
    p.concept_fem.loads.add_load_case_combination(LoadConceptCaseCombination("LCC", [term]))
    with conversion_report.collect() as report:
        p.fem = p.to_fem_obj(0.5, "line")
    (step,) = p.fem.steps
    assert list(step.load_cases) == ["LC"]
    (f,) = [f for f in report.findings if f.subject == "LCC" and f.kind == "omitted"]
    assert (f.keyword, f.details) == ("LoadConceptCaseCombination", {"load_case": "LC", "phase": 90})


# --- GeniE's load case numbers are the deck's -----------------------------------------------------------------------


def _numbered(name, numbers):
    from ada.fem.concept.loads import (
        LoadConceptCase,
        LoadConceptCaseCombination,
        LoadConceptCaseFactored,
        LoadConceptPoint,
    )

    p = ada.Part(name) / ada.Beam("bm", (0, 1.5, 0), (4, 1.5, 0), "IPE300")
    a = ada.Assembly(f"{name}_a") / p
    cases = []
    for i, number in enumerate(numbers, start=1):
        load = LoadConceptPoint(f"P{i}", (i, 1.5, 0), (0, 0, -1000.0 * i), (0, 0, 0))
        cases.append(p.concept_fem.loads.add_load_case(LoadConceptCase(f"LC{i}", [load], fem_loadcase_number=number)))
    terms = [LoadConceptCaseFactored(lc, 1.0) for lc in cases]
    p.concept_fem.loads.add_load_case_combination(LoadConceptCaseCombination("LCC", terms))
    return a, p


def _llc(deck) -> dict[str, list[int]]:
    recs = _records(deck)
    out = {"TDLOAD": [(int(v[1]), v[-1]) for name, v in recs if name == "TDLOAD"]}
    out["BNLOAD"] = sorted({int(v[0]) for name, v in recs if name == "BNLOAD"})
    return out


def test_the_cases_are_written_under_genies_numbers_and_a_combination_after_them(tmp_path):
    """GeniE writes TDLOAD and its load records under ``fem_loadcase_number``; Sestra V11.3 then gives each result case
    that number as its IERES (measured on GeniE V8.13-02's own deck with cases 5 and 9: IRES 1, 2 with IERES 5, 9).
    adapy wrote 1 and 2. A combination has no number and takes the next one up."""
    a, p = _numbered("num", (5, 9))
    deck, report = _write(a, p, tmp_path)
    assert _llc(deck) == {"TDLOAD": [(5, "LC1"), (9, "LC2"), (10, "LCC")], "BNLOAD": [5, 9, 10]}
    assert not [f for f in report.findings if f.keyword == "TDLOAD"]


def test_cases_sharing_a_number_are_numbered_in_order_and_named(tmp_path):
    a, p = _numbered("dup", (3, 3))
    deck, report = _write(a, p, tmp_path)
    assert _llc(deck) == {"TDLOAD": [(1, "LC1"), (2, "LC2"), (3, "LCC")], "BNLOAD": [1, 2, 3]}
    (f,) = [f for f in report.findings if f.keyword == "TDLOAD"]
    assert (f.kind, f.details["shared"]) == ("note", {3: ["LC1", "LC2"]})


class _Sin:
    """The records ``result_case_names`` reads, as Sestra V11.3 wrote them for GeniE's cases numbered 5 and 9."""

    type_blocks = {"TDLOAD": 1, "TDRESREF": 1, "RDRESREF": 1}

    def __init__(self, tdresref=True):
        if not tdresref:
            self.type_blocks = {"TDLOAD": 1, "RDRESREF": 1}

    def iter_records(self, card):
        return iter([[1.0, 1.0, 5.0, 0.0, 0.0, 1.0, 10.0, 5.0, 0.0], [2.0, 1.0, 9.0, 0.0, 0.0, 1.0, 10.0, 9.0, 0.0]])

    def iter_text_records(self, card):
        if card == "TDLOAD":
            return iter([((5.0, 103.0, 0.0), "LCa"), ((9.0, 103.0, 0.0), "LCb")])
        return iter([((1.0, 108.0, 0.0), "LCa"), ((2.0, 108.0, 0.0), "LCb")])


@pytest.mark.parametrize("tdresref", [True, False])
def test_a_load_case_name_goes_to_the_result_case_it_became(tdresref):
    """TDLOAD's 5 and 9 are load case numbers; the results are IRES 1 and 2. Read as result cases they named cases 5
    and 9, which do not exist: ``{5: LCa, 9: LCb, 1: LCa, 2: LCb}``."""
    from ada.fem.formats.sesam.results.case_names import result_case_names

    assert result_case_names(_Sin(tdresref)) == {1: "LCa", 2: "LCb"}


def test_a_sub_parts_combination_is_converted_and_not_also_reported_unconverted(tmp_path):
    """The meshed part ``Top`` holds ``Sub``, which owns LC1 and LCC = 2 LC1. LCC was converted but recorded under
    ``Top``, and the writer's check, which looks under the owner, then also reported "LCC of part Sub" omitted."""
    from ada.fem.concept.loads import (
        LoadConceptCase,
        LoadConceptCaseCombination,
        LoadConceptCaseFactored,
        LoadConceptPoint,
    )

    sub = ada.Part("Sub") / ada.Beam("bm", (0, 1.5, 0), (4, 1.5, 0), "IPE300")
    top = ada.Part("Top") / sub
    a = ada.Assembly("A") / top
    lc = sub.concept_fem.loads.add_load_case(
        LoadConceptCase("LC1", [LoadConceptPoint("P", (4, 1.5, 0), (0, 0, -1000.0), (0, 0, 0))])
    )
    sub.concept_fem.loads.add_load_case_combination(
        LoadConceptCaseCombination("LCC", [LoadConceptCaseFactored(lc, 2.0)])
    )
    deck, report = _write(a, top, tmp_path)
    assert resultants(deck)["LCC"][0] == (0.0, 0.0, -2000.0)
    assert not [f for f in report.findings if f.kind == "omitted" and f.keyword.startswith("LoadConcept")]


def test_a_point_load_at_a_rigid_link_master_is_named_as_on_a_reference_node(tmp_path):
    """A 4 x 1 plate held at its short edges, a rigid link whose master is 1 m above the middle, and 1000 N down at the
    master point. The master is a node of its own that no element connects; GeniE V8.13-02, meshing the same model
    (master free, and fixed), wrote no load record for it -- LC1 empty -- so adapy writes none either. The report used
    to say the mesh had no node there, which was not true."""
    from ada.fem.concept.constraints import ConstraintConceptCurve
    from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
    from ada.fem.concept.constraints import ConstraintConceptRigidLink, RigidLinkRegion
    from ada.fem.concept.loads import LoadConceptPoint

    a, p, _ = _plate_part("rlp", LoadConceptPoint("PL1", (2, 0.5, 1), (0, 0, -1000.0), (0, 0, 0)))
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("Sc1", (0, 0, 0), (0, 1, 0), Dof.encastre()))
    c.add_curve_constraint(ConstraintConceptCurve("Sc2", (4, 0, 0), (4, 1, 0), Dof.encastre()))
    free = [Dof(d, "free") for d in ("dx", "dy", "dz", "rx", "ry", "rz")]
    region = RigidLinkRegion((1.5, 0, -0.1), (2.5, 1, 0.1))
    c.add_rigid_link(ConstraintConceptRigidLink("Srl", (2, 0.5, 1), region, free, rotation_dependent=False))
    with conversion_report.collect() as report:
        fem = p.to_fem_obj(0.5, use_quads=True)
    (master,) = [n for n in fem.nodes if abs(n.z - 1) < 1e-9]
    (f,) = [f for f in report.findings if f.subject == "PL1 in load case LC"]
    assert f.kind == "omitted" and "reference node" in f.reason and f.details["nodes"] == [master.id]
    assert fem.steps[0].load_cases["LC"].loads == []

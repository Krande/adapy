"""A line load (:class:`~ada.fem.LoadLine`) through the Calculix and Code_Aster writers.

Both writers raised on one (Calculix ``ValueError: Calculix does not accept Loads without reference to a fem_set``,
Code_Aster ``NotImplementedError: Load type "line"``), so a model with a line load in a step they write did not
write at all. Calculix writes every segment as nodal loads (``*CLOAD``); Code_Aster writes a beam element loaded
uniformly end to end as ``FORCE_POUTRE`` and the rest as ``FORCE_NODALE``, over mesh groups of its own. On a two-node
beam (CalculiX U1, Code_Aster POU_D_E, both Euler-Bernoulli) the nodal loads are the Hermite-consistent forces and
moments (:meth:`LoadLine.hermite_nodal_loads`), on a shell edge the forces of the linear edge
(:meth:`LoadLine.nodal_loads`).

Nothing is solved here (``tests/fem/test_calculix_code_aster_solve.py`` solves): each deck's records are summed back
into a force and a moment and compared with the load's own, and the concept load's closed form. The part: a 4 m beam at y = 1.5 under a
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
    """Write the part with ``case``'s line load as the one load of an assembly step and return the deck's text, the
    line load and the conversion report. Both writers write the part's own step (its concept load cases) as well; it
    is set aside here so that the deck carries this one load."""
    a, p, loads = meshed
    load = LoadLine(f"ll_{case}", loads[case].segments)
    step = a.fem.add_step(StepImplicitStatic("s1"))
    step.add_load(load)
    part_steps = list(p.fem.steps)
    for s in part_steps:
        p.fem.steps.remove(s)
    try:
        with conversion_report.collect() as report:
            a.to_fem(case, fmt, scratch_dir=tmp_path, overwrite=True)
    finally:
        a.fem.steps.remove(step)
        p.fem.steps.extend(part_steps)
    suffix = ".inp" if fmt == "calculix" else ".comm"
    return (tmp_path / case / case).with_suffix(suffix).read_text(), load, report


def _summed(points_forces) -> tuple[np.ndarray, np.ndarray]:
    """The total force and its moment about the origin of ``(position, load)`` pairs, a load being a force or a force
    and a nodal moment (six components)."""
    f, m = np.zeros(3), np.zeros(3)
    for pos, load in points_forces:
        load = np.concatenate([load, np.zeros(6 - len(load))])
        f += load[:3]
        m += np.cross(pos, load[:3]) + load[3:]
    return f, m


def _expected(load: LoadLine) -> tuple[np.ndarray, np.ndarray]:
    return load.resultant(), sum((s.moment() for s in load.segments), np.zeros(3))


# --- Calculix --------------------------------------------------------------------------------------------------------


def _ccx_cloads(inp: str, p) -> list[tuple[np.ndarray, np.ndarray]]:
    (block,) = re.findall(r"^\*Cload\n((?:\d+, \d, \S+\n)+)", inp, re.M)
    out = []
    for nid, dof, value in re.findall(r"^(\d+), (\d), (\S+)$", block, re.M):
        force = np.zeros(6)
        force[int(dof) - 1] = float(value)
        out.append((np.asarray(p.fem.nodes.from_id(int(nid)).p, dtype=float), force))
    return out


@pytest.mark.parametrize("case", list(CASES))
def test_calculix_writes_a_line_load_as_nodal_forces_summing_to_the_load(meshed, case, tmp_path):
    inp, load, report = _write(meshed, "calculix", case, tmp_path)
    _, p, _ = meshed
    loads = inp.split("** LOADS")[1]
    assert not re.search(r"^\*Dload$", loads, re.M), "no distributed load (the step's '*Dload, OP=NEW' only clears)"
    force, moment = _summed(_ccx_cloads(inp, p))
    want_f, want_m = _expected(load)
    assert force == pytest.approx(want_f, abs=1e-6)
    assert force == pytest.approx(np.asarray(CASES[case][1]), abs=1e-6)
    assert moment == pytest.approx(want_m, abs=1e-6)
    notes = [f for f in report.findings if f.keyword == "LoadLine"]
    if case == "LC_edge":  # a shell edge: forces of the linear edge, said so
        (note,) = notes
        assert (note.kind, note.stage, note.subject) == ("note", "calculix writer", load.name)
    else:  # a U1 beam: Hermite-consistent forces and moments, exact at the nodes; nothing to report
        assert notes == []


#: The Hermite-consistent loads of a uniform q over the first c of an element of length h, at its first end (force,
#: moment), integrated by hand: F1 = q (c - c^3/h^2 + c^4/(2 h^3)), M1 = q (c^2/2 - 2 c^3/(3 h) + c^4/(4 h^2)); at its
#: second end F2 = q c - F1, M2 = -q (c^3/(3 h) - c^4/(4 h^2)).
def _hermite_partial(q, c, h):
    f1 = q * (c - c**3 / h**2 + c**4 / (2 * h**3))
    m1 = q * (c**2 / 2 - 2 * c**3 / (3 * h) + c**4 / (4 * h**2))
    return f1, m1, q * c - f1, -q * (c**3 / (3 * h) - c**4 / (4 * h**2))


def test_calculix_nodal_loads_are_the_hermite_ones(meshed, tmp_path):
    """The partial load 1.3..2.9 m on 0.5 m elements, on U1 beams. The element 2.5..3.0 is loaded over 2.5..2.9
    (c = 0.4): 246.4 N and 20.2667 N m at 2.5, 153.6 N and -17.0667 N m at 3.0; the element 2.0..2.5 is loaded whole,
    250 N and +-20.8333 N m (q h^2 / 12) at its ends. Forces alone, as this wrote before, leave a simply supported
    beam's mid-span short by 0.8 (h / L)^2 under a uniform load."""
    inp, _, _ = _write(meshed, "calculix", "LC_part", tmp_path)
    _, p, _ = meshed
    by_x = {}
    for pos, load in _ccx_cloads(inp, p):
        x = round(float(pos[0]), 6)
        by_x[x] = by_x.get(x, np.zeros(6)) + load
    q, h = -1000.0, 0.5
    f1, m1, f2, m2 = _hermite_partial(q, 0.4, h)
    whole_f, whole_m = q * h / 2, q * h**2 / 12
    # the moment of a load along -z on a beam along +x is about -y for the near end: M = (t x q) int N2 dx
    assert by_x[2.5][2] == pytest.approx(whole_f + f1, abs=1e-9)
    assert by_x[2.5][4] == pytest.approx(whole_m - m1, abs=1e-9)  # the whole element's end moment, then the partial one
    assert by_x[3.0][2] == pytest.approx(f2, abs=1e-9)
    assert by_x[3.0][4] == pytest.approx(-m2, abs=1e-9)
    assert (f1, m1, f2, m2) == pytest.approx((-246.4, -20.266666667, -153.6, 17.066666667))


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


def _aster_concept(load: LoadLine) -> str:
    """The concept a load becomes in the command file: the writer's prefix and the load's name (an identifier here,
    so not renamed -- :mod:`ada.fem.formats.code_aster.write.names`)."""
    from ada.fem.formats.code_aster.write.names import PREFIX

    return PREFIX["load"] + load.name


def _aster_records(comm: str, load: LoadLine, p, groups) -> tuple[list, list]:
    """The load's ``FORCE_POUTRE`` rows as (element, q) and ``FORCE_NODALE`` rows as (node position, force)."""
    m = re.search(rf"^{_aster_concept(load)} = AFFE_CHAR_MECA\(\n(.*?)^\)$", comm, re.M | re.S)
    assert m, f"no AFFE_CHAR_MECA for {load.name}"
    body = m[1]
    num = r"(-?[\d.eE+-]+)"
    comps = ("FX", "FY", "FZ", "MX", "MY", "MZ")
    beam = [
        (p.fem.elements.from_id(el), np.array([float(fx), float(fy), float(fz)]))
        for name, fx, fy, fz in re.findall(rf"_F\(GROUP_MA='(\w+)', FX={num}, FY={num}, FZ={num}\)", body)
        for el in sorted(groups[name])
    ]
    nodal = []
    for name, rest in re.findall(r"_F\(GROUP_NO='(\w+)', ([^)]*)\)", body):
        values = dict(re.findall(rf"(\w+)={num}", rest))
        load = np.array([float(values.get(c, 0.0)) for c in comps])
        nodal += [(np.asarray(p.fem.nodes.from_id(n).p, dtype=float), load) for n in sorted(groups[name])]
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
    if case == "LC_edge":  # a shell edge: forces of the linear edge, said so; a beam's nodal loads are Hermite's, exact
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
        concept = _aster_concept(load)
        body = comm.split(f"{concept} = AFFE_CHAR_MECA(")[1].split("\n)\n")[0]
        assert {k for k in ("FORCE_POUTRE", "FORCE_NODALE") if k in body} == kinds
        # the step loads the concept the load was written as
        assert "EXCIT=(" in comm and f"_F(CHARGE={concept})," in comm


def test_code_aster_takes_its_line_load_groups_off_the_model_again(meshed, tmp_path):
    _, p, _ = meshed
    before = (sorted(p.fem.elsets), sorted(p.fem.nsets))
    _write(meshed, "code_aster", "LC_part", tmp_path)
    assert (sorted(p.fem.elsets), sorted(p.fem.nsets)) == before


# --- second-order shell edges ------------------------------------------------------------------------------------


def _second_order_plate(quads: bool):
    """A 4 x 1 m plate in 8-node quads (or 6-node triangles) at 0.5 m, the edge load of LC_edge along y = 0 and a
    linear one (1000 -> 3000 N/m down) along it too."""
    from ada.fem.meshing import GmshOptions

    pl = ada.Plate.from_3d_points("pl", [(0, 0, 0), (4, 0, 0), (4, 1, 0), (0, 1, 0)], 0.01)
    p = ada.Part("p") / pl
    a = ada.Assembly("a") / p
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("fix", (0, 1, 0), []))
    edge, _ = CASES["LC_edge"]
    lin = LoadConceptLine("elin", (0, 0, 0), (4, 0, 0), (0, 0, -1000.0), (0, 0, -3000.0))
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC_edge", [edge], fem_loadcase_number=1))
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC_elin", [lin], fem_loadcase_number=2))
    with conversion_report.collect() as report:
        p.fem = p.to_fem_obj(0.5, use_quads=quads, options=GmshOptions(Mesh_ElementOrder=2))
    (step,) = p.fem.steps
    return a, p, {name: lc.loads for name, lc in step.load_cases.items()}, report


@pytest.mark.parametrize("quads", [True, False], ids=["quad8", "tri6"])
def test_a_line_load_along_second_order_shell_edges_is_their_consistent_nodal_loads(quads):
    """An edge load along a plate meshed in 8-node quads or 6-node triangles became no load at all -- reported
    ``omitted``, "no beam element or shell element edge lies along it" -- because only 3- and 4-node shell edges were
    looked at. Now each edge carries it to its three nodes: ``L q_a / 6``, ``L (q_a + q_b) / 3``, ``L q_b / 6``
    (uniform: 1/6, 4/6, 1/6 of ``q L``), exact in the resultant and its moment."""
    from ada.fem.shapes.definitions import ShellShapes

    a, p, loads, report = _second_order_plate(quads)
    assert {el.type for el in p.fem.elements} == {ShellShapes.QUAD8 if quads else ShellShapes.TRI6}
    assert not report.of_kind("omitted"), report.summary()
    for name, total, moment_y in (
        ("LC_edge", (400, 0, -2000.0), 2000.0 * 2),
        ("LC_elin", (0, 0, -8000.0), 8000.0 * 4 * 7 / 12),
    ):
        (load,) = loads[name]
        assert isinstance(load, LoadLine) and all(s.edge is not None for s in load.segments)
        assert len(load.segments) == 8
        nodal = LoadLine.summed_nodal_loads(load.segments)
        assert len(nodal) == 17, "8 edges: 9 corner nodes and 8 midside nodes"
        force = sum((f for _, f in nodal), np.zeros(3))
        assert force == pytest.approx(total, abs=1e-9)
        my = sum(n.p[2] * f[0] - n.p[0] * f[2] for n, f in nodal)
        assert my == pytest.approx(moment_y, rel=1e-12)


def test_a_quadratic_edge_shares_a_linear_load_by_its_shape_functions():
    """``N_a = (1 - x)(1 - 2x)``, ``N_m = 4x(1 - x)``, ``N_b = x(2x - 1)`` integrated against ``q`` linear from
    ``q_a`` to ``q_b`` over ``L``: ``L q_a / 6``, ``L (q_a + q_b) / 3``, ``L q_b / 6``. A first-order edge keeps
    ``L (2 q_a + q_b) / 6`` and ``L (q_a + 2 q_b) / 6``."""
    from ada import Node
    from ada.fem import Elem
    from ada.fem.loads import LineLoadSegment
    from ada.fem.shapes.definitions import ShellShapes

    corners = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]
    mids = [(1, 0, 0), (2, 0.5, 0), (1, 1, 0), (0, 0.5, 0)]
    nodes = [Node(p, i) for i, p in enumerate(corners + mids, start=1)]
    quad8 = Elem(1, nodes, ShellShapes.QUAD8)
    quad4 = Elem(2, nodes[:4], ShellShapes.QUAD)
    qa, qb = (0, 0, -1000.0), (0, 0, -3000.0)
    pairs = LoadLine.nodal_loads(LineLoadSegment(quad8, qa, qb, edge=1))
    assert [n.id for n, _ in pairs] == [1, 5, 2]
    assert [f[2] for _, f in pairs] == pytest.approx([-2000 / 6, -2 * 4000 / 3, -6000 / 6], rel=1e-14)
    uniform = LoadLine.nodal_loads(LineLoadSegment(quad8, qa, qa, edge=1))
    assert [f[2] for _, f in uniform] == pytest.approx([-2000 / 6, -2000 * 4 / 6, -2000 / 6], rel=1e-14)
    linear = LoadLine.nodal_loads(LineLoadSegment(quad4, qa, qb, edge=1))
    assert [n.id for n, _ in linear] == [1, 2]
    assert [f[2] for _, f in linear] == pytest.approx([-2 * 5000 / 6, -2 * 7000 / 6], rel=1e-14)


def test_a_quadratic_edge_whose_midside_node_is_off_its_middle_is_refused():
    from ada import Node
    from ada.fem import Elem
    from ada.fem.loads import LineLoadSegment
    from ada.fem.shapes.definitions import ShellShapes

    corners = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]
    mids = [(1, 0.1, 0), (2, 0.5, 0), (1, 1, 0), (0, 0.5, 0)]
    nodes = [Node(p, i) for i, p in enumerate(corners + mids, start=1)]
    with pytest.raises(ValueError, match="midside node 5"):
        LoadLine.nodal_loads(LineLoadSegment(Elem(1, nodes, ShellShapes.QUAD8), (0, 0, -1.0), (0, 0, -1.0), edge=1))

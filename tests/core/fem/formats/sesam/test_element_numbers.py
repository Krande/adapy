"""No solver: a Sesam deck numbers its elements 1..n internally, keeping the model's ids as the external numbers
(write/element_numbers), and every record that names an element uses the internal number. Read back, the model's
ids are what come out. Solved in Sestra in tests/fem/test_sesam_element_numbering.py.
"""

from __future__ import annotations

import numpy as np

import ada
from ada.fem import Bc, FemSet, LoadPressure, Mass, Spring, StepImplicitStatic, Surface
from ada.fem.formats.sesam.write.element_numbers import ElementNumbers, deck, internal
from ada.fem.shapes.definitions import ShellShapes
from ada.materials.metals import CarbonSteel


def _records(text: str, name: str) -> list[list[float]]:
    """Every ``name`` record as its numbers, continuation lines joined."""
    out, cur = [], None
    for line in text.splitlines():
        if line[:1] != " ":
            cur = [] if line.startswith(name) else None
            if cur is not None:
                out.append(cur)
                cur.extend(float(x) for x in line[8:].split())
        elif cur is not None:
            cur.extend(
                float(x)
                for x in line.split()
                if x.replace(".", "").replace("E", "").replace("+", "").replace("-", "").isdigit()
            )
    return out


def test_ids_that_run_one_to_n_are_their_own_internal_numbers():
    numbers = ElementNumbers([3, 1, 2])
    assert numbers.identity and numbers.internal(2) == 2


def test_a_gap_is_closed_in_id_order_and_an_unknown_id_is_itself():
    numbers = ElementNumbers([100, 7, 10_000, 8])
    assert [numbers.internal(i) for i in (7, 8, 100, 10_000)] == [1, 2, 3, 4]
    assert numbers.internal(55) == 55
    with deck(numbers):
        assert internal(100) == 3
    assert internal(100) == 100  # outside a deck the id is the number


def _plate():
    """A 2 x 1 plate of four shells numbered 100, 101, 105, 109, a grounded spring 10000 and a mass element 500 on a
    corner, a set of two of the shells, and a pressure on the plate."""
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01, mat=mat)
    p = ada.Part("P") / pl
    p.fem = pl.to_fem_obj(0.5, "shell")
    fem = p.fem
    shells = sorted(fem.elements.shell, key=lambda e: e.id)
    new_ids = [100 + 3 * i + (i % 2) for i in range(len(shells))]  # gaps everywhere
    for el, nid in zip(shells, new_ids):
        el.id = nid
    fem.elements.renumber(renumber_map={e.id: e.id for e in fem.elements})
    corner = min(fem.nodes, key=lambda n: (n.x, n.y))
    fem.add_spring(Spring("spr", 10_000, "SPRING1", np.diag([1e6] * 6), fem.add_set(FemSet("spr_n", [corner], "nset"))))
    fem.add_mass(Mass("m", [corner], 5.0, mass_id=500))
    two = fem.add_set(FemSet("two", shells[1:3], "elset"))
    fem.add_bc(Bc("fix", fem.add_set(FemSet("edge", [n for n in fem.nodes if n.x < 1e-9], "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    every = fem.add_set(FemSet("all_shells", shells, "elset"))
    surf = fem.add_surface(Surface("top", Surface.TYPES.ELEMENT, every, el_face_index=1))
    a.fem.add_step(StepImplicitStatic("press")).add_load(LoadPressure("q", 1000.0, surf))
    return a, fem, shells, two


def test_every_record_that_names_an_element_uses_its_internal_number(tmp_path):
    a, fem, shells, two = _plate()
    a.to_fem("deck", "sesam", scratch_dir=tmp_path, overwrite=True)
    text = next((tmp_path / "deck").glob("*T1.FEM")).read_text()

    gelmnt = _records(text, "GELMNT1")
    external = [int(r[0]) for r in gelmnt]
    internal_no = [int(r[1]) for r in gelmnt]
    assert internal_no == list(range(1, len(gelmnt) + 1)), "internal numbers 1..n, in GELMNT1 order"
    assert external == sorted(external) and {500, 10_000} <= set(external)
    assert set(int(e.id) for e in shells) <= set(external)
    of = dict(zip(external, internal_no))
    assert min(int(e.id) for e in shells) >= 100 and any(of[x] != x for x in external), "not vacuous: ids have gaps"

    assert sorted(int(r[0]) for r in _records(text, "GELREF1")) == internal_no
    assert {int(r[4]) for r in _records(text, "BEUSLO")} == {of[int(e.id)] for e in shells}
    # the element set "two": its members by internal number (GSETMEMB ISTYPE 2, after NFIELD ISREF INDEX ISTYPE ISORIG)
    names = {}
    for r in _records(text, "TDSETNAM"):
        names[int(r[1])] = r
    sets = [r for r in _records(text, "GSETMEMB") if int(r[3]) == 2]
    members = {tuple(sorted(int(x) for x in r[5:])) for r in sets}
    assert tuple(sorted(of[int(e.id)] for e in two.members)) in members


def test_read_back_the_model_ids_and_sets_come_back(tmp_path):
    a, fem, shells, two = _plate()
    a.to_fem("deck", "sesam", scratch_dir=tmp_path, overwrite=True)
    b = ada.from_fem(next((tmp_path / "deck").glob("*T1.FEM")))
    (part,) = [q for q in b.get_all_parts_in_assembly() if len(q.fem.elements)]
    back = part.fem
    ids = {int(e.id) for e in back.elements}
    assert {int(e.id) for e in shells} | {500, 10_000} <= ids
    assert sorted(int(e.id) for e in back.elsets["two"].members) == sorted(int(e.id) for e in two.members)
    assert {int(e.id) for e in back.elements.shell} == {int(e.id) for e in shells}
    assert all(e.type in (ShellShapes.QUAD, ShellShapes.TRI) for e in back.elements.shell)
    (step,) = back.steps
    ((_, lc),) = step.load_cases.items()
    (load,) = lc.loads
    assert {int(e.id) for e in load.surface.fem_set.members} == {int(e.id) for e in shells}

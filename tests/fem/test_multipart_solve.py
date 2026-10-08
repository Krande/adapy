"""A model of two parts, solved by the single-part writers' solvers: the merge must carry each part's
supports, the assembly's own supports and every load onto the nodes they were defined on.

Two 1 x 1 m, 10 mm plates (PartA at x = 0, PartB at x = 10), each meshed at 0.5 m and clamped on its
own x = x0 edge. Both parts number their nodes from 1, so the merge renumbers PartB.

Measured before the fix: every leg stopped in the merge with ``AttributeError: property 'fem_set' of
'Bc' object has no setter``. Past that (setter alone), Sestra V11.3-00 put a point load on PartB's tip
(PartB node 4, merged node 13) on PartA's node 4 -- PartA moved (node 4: 0.0251), every PartB node
stayed at 0.0 -- and an assembly-level Bc was in no deck.

Measured after it (CalculiX 2.23, Code_Aster 18.1.8, Sestra V11.3-00), ``u3`` in metres:

=================================  ==============  ===============  ===============
leg                                CalculiX        Code_Aster       Sestra
=================================  ==============  ===============  ===============
gravity, free-edge mid, each plate -3.15978e-05    -5.6479774e-03   -5.2545792e-03
  largest relative A/B difference  0               1.1e-14          0
pressure on PartB, PartB tip       7.03114e-05     6.9253865e-03    6.8859956e-03
point load on PartB tip            --              --               -2.5134942e-02
  every PartA node                 0.0             0.0              0.0
assembly Bc, PartB corner          0.0             0.0              0.0
  same corner of PartA             -4.73233e-05    -5.6209475e-03   -5.1408536e-03
=================================  ==============  ===============  ===============

(CalculiX expands the three-node shells into solids, which are far stiffer on this two-element-wide
mesh; the comparisons below are between the plates of one run, so that is not what they measure.)
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, Load, StepImplicitStatic, Surface
from ada.fem.loads import LoadPressure

FORMATS = ["calculix", "code_aster", "sesam"]


def _plate_part(name: str, x0: float) -> ada.Part:
    pl = ada.Plate("pl", [(x0, 0), (x0 + 1, 0), (x0 + 1, 1), (x0, 1)], 0.01)
    p = ada.Part(name) / pl
    p.fem = pl.to_fem_obj(0.5, "shell")
    edge = [n for n in p.fem.nodes if abs(n.x - x0) < 1e-9]
    tip = [n for n in p.fem.nodes if abs(n.x - (x0 + 1)) < 1e-9 and abs(n.y - 1.0) < 1e-9]
    p.fem.sets.add(FemSet("edge", edge, FemSet.TYPES.NSET, parent=p.fem))
    p.fem.sets.add(FemSet("tip", tip, FemSet.TYPES.NSET, parent=p.fem))
    plate = p.fem.sets.add(FemSet("plate", list(p.fem.elements), FemSet.TYPES.ELSET, parent=p.fem))
    p.fem.add_surface(Surface("top", Surface.TYPES.ELEMENT, plate, parent=p.fem))
    p.fem.add_bc(Bc("fix", p.fem.nsets["edge"], [1, 2, 3, 4, 5, 6]))
    return p


def _two_plates() -> tuple[ada.Assembly, StepImplicitStatic]:
    a = ada.Assembly("A")
    a.add_part(_plate_part("PartA", 0.0))
    a.add_part(_plate_part("PartB", 10.0))
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=1.0, total_time=1.0, max_incr=1.0))
    return a, step


def _uz(a: ada.Assembly, fem_format: str, name: str, tmp_path) -> dict[tuple[float, float], float]:
    """``u3`` of every node, by its (x, y) position (the merged ids are the merge's business)."""
    from ada.fem.formats.utils import default_fem_res_path
    from ada.fem.results.field_data import NodalFieldType

    res = a.to_fem(name, fem_format, scratch_dir=tmp_path, overwrite=True, execute=True, exit_on_complete=False)
    if res is None:
        res = ada.from_fem_res(default_fem_res_path(name, scratch_dir=tmp_path, fem_format=fem_format))
    coords = np.asarray(res.mesh.nodes.coords, dtype=float)
    field = [
        f
        for f in res.results
        if getattr(f, "field_type", None) == NodalFieldType.DISP or f.name in ("DISP", "result__DEPL")
    ][-1]
    col = next(i for i, c in enumerate(field.components) if c.upper() in ("U3", "D3", "DZ", "Z"))
    values = np.asarray(field.values, dtype=float)
    # CalculiX reports a shell as the solid it expands it into: two nodes per shell node, at z = +-t/2.
    by_position: dict = {}
    for (x, y, _), v in zip(coords, values):
        by_position.setdefault((round(float(x), 6), round(float(y), 6)), []).append(float(v[col + 1]))
    assert len(by_position) == 18
    return {pos: float(np.mean(vs)) for pos, vs in by_position.items()}


def _of(uz: dict, x0: float) -> dict[tuple[float, float], float]:
    """One plate's ``u3`` by its position relative to its own clamped edge."""
    return {(round(x - x0, 6), y): v for (x, y), v in uz.items() if x0 - 1e-6 <= x <= x0 + 1 + 1e-6}


@pytest.mark.parametrize("fem_format", FORMATS)
def test_two_clamped_plates_deflect_alike(fem_format, tmp_path, require_solver):
    require_solver(fem_format)
    a, step = _two_plates()
    step.add_load(ada.fem.LoadGravity("grav", -9.81))

    uz = _uz(a, fem_format, f"twin_{fem_format}", tmp_path)
    plate_a, plate_b = _of(uz, 0.0), _of(uz, 10.0)

    assert sorted(plate_a) == sorted(plate_b)
    free_mid = plate_a[(1.0, 0.5)]
    assert free_mid < 0.0
    # Measured worst relative difference: 1.1e-14 (Code_Aster); 0 for CalculiX and Sestra.
    for pos, value in plate_a.items():
        assert plate_b[pos] == pytest.approx(value, rel=1e-12, abs=1e-18), pos


@pytest.mark.parametrize(
    "fem_format, kind",
    [("sesam", "point"), ("sesam", "pressure"), ("calculix", "pressure"), ("code_aster", "pressure")],
)
def test_a_load_on_the_second_part_moves_only_the_second_part(fem_format, kind, tmp_path, require_solver):
    require_solver(fem_format)
    a, step = _two_plates()
    pb = a.get_part("PartB")
    if kind == "point":
        step.add_load(Load("tipload", Load.TYPES.FORCE, -1000.0, dof=3, fem_set=pb.fem.nsets["tip"]))
    else:
        step.add_load(LoadPressure("q", 1000.0, pb.fem.surfaces["top"]))

    uz = _uz(a, fem_format, f"onb_{fem_format}_{kind}", tmp_path)
    plate_a, plate_b = _of(uz, 0.0), _of(uz, 10.0)

    assert all(v == 0.0 for v in plate_a.values()), plate_a
    assert plate_b[(1.0, 1.0)] != 0.0


@pytest.mark.parametrize("fem_format", FORMATS)
def test_an_assembly_level_bc_holds_in_the_solve(fem_format, tmp_path, require_solver):
    require_solver(fem_format)
    a, step = _two_plates()
    step.add_load(ada.fem.LoadGravity("grav", -9.81))
    pb = a.get_part("PartB")
    corner = [n for n in pb.fem.nodes if abs(n.x - 11.0) < 1e-9 and abs(n.y) < 1e-9]
    a.fem.add_bc(Bc("asm_fix", FemSet("asm_corner", corner, FemSet.TYPES.NSET), [3]))

    uz = _uz(a, fem_format, f"asmbc_{fem_format}", tmp_path)
    plate_a, plate_b = _of(uz, 0.0), _of(uz, 10.0)

    assert plate_b[(1.0, 0.0)] == 0.0
    # The same corner of the plate without that Bc hangs free.
    assert plate_a[(1.0, 0.0)] < 0.0

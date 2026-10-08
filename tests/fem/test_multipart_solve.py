"""A model of two parts, solved by the single-part writers' solvers: the merge must carry each part's
supports, the assembly's own supports, every load and each part's material onto the nodes and elements
they were defined on.

Two 1 x 1 m, 10 mm plates (PartA at x = 0, PartB at x = 10), each meshed with 2 x 2 four-node shells
(0.5 m) and clamped on its own x = x0 edge, the other three edges free. Both parts number their nodes from
1, so the merge renumbers PartB.

Closed form, E = 2.1e11, nu = 0.3, t = 0.01, L = 1, a uniform load q: the clamped strip in cylindrical
bending deflects w = q L^4 / (8 D) at its free edge, D = E t^3 / (12 (1 - nu^2)) = 19231 Nm. Gravity
(q = rho g t = 7850 * 9.81 * 0.01 = 770.09 Pa): 5.0056e-3 m; 1000 Pa: 6.5000e-3 m. (The strip as a beam,
without the (1 - nu^2): 5.5006e-3 and 7.1429e-3.) A plate with free sides lies a little above the
cylindrical-bending value; refined to 0.125 m, Code_Aster gives 1.039 of it, Sestra 1.039 (pressure) and
1.035 (gravity), CalculiX S4 1.018, rising.

Measured before the fix: every leg stopped in the merge with ``AttributeError: property 'fem_set' of
'Bc' object has no setter``. Past that (setter alone), Sestra V11.3-00 put a point load on PartB's tip
(PartB node 4, merged node 13) on PartA's node 4 -- PartA moved, every PartB node stayed at 0.0 -- and
an assembly-level Bc was in no deck.

Measured after it (CalculiX 2.23, Code_Aster 18.1.8, Sestra V11.3-00), ``u3`` in metres, (ratio to the
closed form):

=================================  ===================  ===================  ===================
leg                                CalculiX (S4)        Code_Aster (DKQ)     Sestra
=================================  ===================  ===================  ===================
gravity, free-edge mid, each plate -5.0095e-03 (1.001)  -5.6220e-03 (1.123)  -5.2872e-03 (1.056)
  largest relative A/B difference  0                    0                    0
pressure on PartB, PartB mid       6.5051e-03 (1.001)   7.3005e-03 (1.123)   7.2990e-03 (1.123)
point load on PartB tip            --                   --                   -2.6328e-02
  every PartA node                 0.0                  0.0                  0.0
assembly Bc, PartB corner          0.0                  0.0                  0.0
  same corner of PartA             -5.0061e-03          -5.5028e-03          -5.0817e-03
PartB at E / 3: worst B / 3A - 1   2.1e-06              1.1e-14              6.9e-08
=================================  ===================  ===================  ===================

The ratios to the closed form are the coarse mesh's: each solver's own element on 2 x 2 quads. (Sestra's
gravity and its pressure, as ratios, differ on this mesh and close in as it is refined -- 1.056 / 1.123 at
0.5 m, 1.039 / 1.056 at 0.25 m, 1.035 / 1.039 at 0.125 m; Code_Aster's and CalculiX's are equal.)
The tolerances below are those measured deviations rounded up; a writer that got the thickness, the
density or the units wrong misses by a factor.

On the earlier three-node mesh CalculiX gave -3.15978e-05, 0.6 % of the closed form: it expands S3 into
the linear wedge C3D6, one element through 10 mm at 0.5 m in plane, which shear-locks (31 % of the beam
strip at 0.05 m). S3 -> C3D6 shear locking: 0.6 % of the closed form on this mesh; S4 gives 100.1 %.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, Load, StepImplicitStatic, Surface
from ada.fem.loads import LoadPressure
from ada.materials.metals import CarbonSteel

FORMATS = ["calculix", "code_aster", "sesam"]

E, NU, RHO, G, T, L = 2.1e11, 0.3, 7850.0, 9.81, 0.01, 1.0
D = E * T**3 / (12 * (1 - NU**2))
#: Free-edge deflection of the clamped strip in cylindrical bending, q L^4 / (8 D).
W_GRAVITY = RHO * G * T * L**4 / (8 * D)  # 5.0056e-3 m
W_PRESSURE = 1000.0 * L**4 / (8 * D)  # 6.5000e-3 m
#: Measured ratio to the closed form at the free-edge mid, minus one, on this mesh, rounded up.
TOL_GRAVITY = {"calculix": 2e-3, "code_aster": 0.13, "sesam": 0.06}  # measured 7.9e-4, 0.123, 0.056
TOL_PRESSURE = {"calculix": 2e-3, "code_aster": 0.13, "sesam": 0.13}  # measured 7.9e-4, 0.123, 0.123


def _plate_part(name: str, x0: float, E: float = 2.1e11) -> ada.Part:
    mat = ada.Material("S355", CarbonSteel("S355", E=E))
    pl = ada.Plate("pl", [(x0, 0), (x0 + 1, 0), (x0 + 1, 1), (x0, 1)], 0.01, mat=mat)
    p = ada.Part(name) / pl
    p.fem = pl.to_fem_obj(0.5, "shell", use_quads=True)
    edge = [n for n in p.fem.nodes if abs(n.x - x0) < 1e-9]
    tip = [n for n in p.fem.nodes if abs(n.x - (x0 + 1)) < 1e-9 and abs(n.y - 1.0) < 1e-9]
    p.fem.sets.add(FemSet("edge", edge, FemSet.TYPES.NSET, parent=p.fem))
    p.fem.sets.add(FemSet("tip", tip, FemSet.TYPES.NSET, parent=p.fem))
    plate = p.fem.sets.add(FemSet("plate", list(p.fem.elements), FemSet.TYPES.ELSET, parent=p.fem))
    p.fem.add_surface(Surface("top", Surface.TYPES.ELEMENT, plate, parent=p.fem))
    p.fem.add_bc(Bc("fix", p.fem.nsets["edge"], [1, 2, 3, 4, 5, 6]))
    return p


def _two_plates(e_b: float = 2.1e11) -> tuple[ada.Assembly, StepImplicitStatic]:
    a = ada.Assembly("A")
    a.add_part(_plate_part("PartA", 0.0))
    a.add_part(_plate_part("PartB", 10.0, E=e_b))
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
    assert plate_a[(1.0, 0.5)] == pytest.approx(-W_GRAVITY, rel=TOL_GRAVITY[fem_format])
    # Measured worst relative difference: 0 in all three (1.1e-14 for Code_Aster on three-node shells).
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
    if kind == "pressure":
        assert plate_b[(1.0, 0.5)] == pytest.approx(W_PRESSURE, rel=TOL_PRESSURE[fem_format])
    else:
        assert plate_b[(1.0, 1.0)] < 0.0


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


@pytest.mark.parametrize("fem_format", FORMATS)
def test_a_part_of_a_third_the_stiffness_deflects_three_times_as_much(fem_format, tmp_path, require_solver):
    """Both parts' material is named "S355"; PartB's has E = 7e10. Before the merge kept them apart,
    every writer gave PartB PartA's material (one ``DEFI_MATERIAU`` / ``*Material`` / ``MISOSEL``) and the
    two plates deflected alike."""
    require_solver(fem_format)
    a, step = _two_plates(e_b=7.0e10)
    step.add_load(ada.fem.LoadGravity("grav", -9.81))

    uz = _uz(a, fem_format, f"twomat_{fem_format}", tmp_path)
    plate_a, plate_b = _of(uz, 0.0), _of(uz, 10.0)

    assert plate_a[(1.0, 0.5)] < 0.0
    # The result files' own precision: CalculiX's .frd prints six significant digits (measured worst
    # B / 3A - 1: 2.1e-6), Sestra's .SIN holds single precision (6.9e-8), Code_Aster's .rmed doubles (1.1e-14).
    rel = {"calculix": 1e-5, "sesam": 2.4e-7, "code_aster": 1e-12}[fem_format]
    for pos, value in plate_a.items():
        assert plate_b[pos] == pytest.approx(3.0 * value, rel=rel, abs=1e-18), pos

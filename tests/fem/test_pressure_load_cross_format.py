"""A surface pressure through every deck adapy writes, on one plate meshed by gmsh.

The model is built the way adapy is meant to build one: an :class:`ada.Plate` from an outline and a
thickness, meshed into shells by the meshing module (``Part.to_fem_obj`` -> gmsh), and then written
out as a deck per format. No mesh is hand-authored here and no solver is driven through an API --
each solver reads a file adapy wrote.

What it pins:

* **Direction.** A positive pressure pushes *into* the face it names. That is Abaqus' convention,
  measured on one S4R (``ada.fem.formats.sesam.write.write_loads``), and it is the one every writer
  has to match, because a load direction that depends on the format makes cross-solver comparison
  meaningless. gmsh winds every element of this strip with its normal along **-z**, so the positive
  face looks down and a positive pressure on it moves the strip **up**.
* **Magnitude.** Mid-span deflection against ``5 q L^4 / (384 D)``, approached at second order on a
  halved mesh and extrapolated onto it.
* **Agreement.** The solvers land on the same number as each other, not merely each near the closed
  form.

Each solver's leg skips when that solver is not installed. Calculix and Code_Aster are both
conda-installable, so the reproducible core of this runs anywhere the ``fem`` environment does;
Abaqus and Sestra join in where they exist.
"""

from __future__ import annotations

import os
import pathlib

import numpy as np
import pytest

import ada
from ada.api.fem_tasks import (
    PLATE_PART_NAME,
    PLATE_STRIP_LENGTH,
    PLATE_STRIP_PRESSURE,
    PLATE_STRIP_WIDTH,
    design_plate_strip,
    mesh_plate_strip,
    plate_closed_form_deflection,
    plate_shell_surface,
)
from ada.fem.formats.general import FEATypes
from ada.fem.loads import LoadPressure

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/pressure_xfmt"

#: Element seed sizes, metres -- 32 / 64 / 128 elements per span. A factor of two apart so the
#: convergence order can be read off the three rather than assumed.
MESH_SIZES = (0.125, 0.0625, 0.03125)

#: The formats whose pressure path this exercises. Sesam is here too: its BEUSLO writer is the one
#: whose sign this convention corrected, so it belongs in the comparison wherever Sestra exists.
FORMATS = ("calculix", "code_aster", "abaqus", "sesam")

#: ``q L b``: the total load, newtons. The one scalar that says the *whole* pressure arrived -- a
#: deck short by one element's load moves mid-span by far less than the discretisation residual.
TOTAL_LOAD = PLATE_STRIP_PRESSURE * PLATE_STRIP_LENGTH * PLATE_STRIP_WIDTH


#: The executable each solver actually installs under, where that differs from the name adapy looks
#: for. ``get_exe_path`` searches for a binary called ``calculix`` / ``code_aster``; conda-forge
#: ships ``ccx`` and ``run_aster``. Without this the whole comparison skips in the very environment
#: built to run it -- the `fem` env, including CI -- which is a green run that measured nothing.
_CONDA_EXE_NAMES = {"calculix": "ccx", "code_aster": "run_aster"}


@pytest.fixture(autouse=True)
def _point_adapy_at_the_conda_solvers(monkeypatch):
    """Set ``ADA_<solver>_exe`` for any solver present under its conda binary name.

    Scoped to this module rather than fixed in ``get_exe_path``, because making adapy discover these
    globally would turn every currently-skipping FEA test into a live solver run -- a change worth
    making deliberately, not as a side effect of adding a test.
    """
    import shutil
    import sys

    for fem_format, exe_name in _CONDA_EXE_NAMES.items():
        env_var = f"ADA_{fem_format}_exe"
        if os.environ.get(env_var):
            continue
        found = shutil.which(exe_name) or (pathlib.Path(sys.prefix) / "bin" / exe_name)
        if found and pathlib.Path(found).exists():
            monkeypatch.setenv(env_var, str(found))


def _solver_available(fem_format: str) -> bool:
    from ada.fem.formats.utils import get_exe_path

    if fem_format == "sesam":
        from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

        try:
            return get_sestra_default_exe_path() is not None
        except Exception:  # noqa: BLE001 - any locator failure is "not installed"
            return False
    try:
        return get_exe_path(FEATypes.from_str(fem_format)) is not None
    except FileNotFoundError:
        return False


def _strip(mesh_size: float, *, negative_face: bool = False) -> ada.Assembly:
    """An ``ada.Plate``, meshed by gmsh, carrying a static step with a pressure on one face."""
    a = design_plate_strip()
    a = mesh_plate_strip(a, mesh_size=mesh_size, elem_order=1, use_quads=True)
    surface = plate_shell_surface(a, negative_face=negative_face)
    step = a.fem.add_step(ada.fem.StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    step.add_load(LoadPressure("q", PLATE_STRIP_PRESSURE, surface))
    return a


def _element_normals_are_negative_z(a: ada.Assembly) -> bool:
    """Every shell's normal along -z, which is what makes the expected signs below what they are."""
    from ada.fem.shapes.definitions import ShellShapes

    fem = a.get_part(PLATE_PART_NAME).fem
    for el in fem.elements:
        if not isinstance(el.type, ShellShapes):
            continue
        p = np.array([[n.x, n.y, n.z] for n in el.nodes[:3]], dtype=float)
        if np.cross(p[1] - p[0], p[2] - p[0])[2] >= 0.0:
            return False
    return True


def _mid_span_u3(res) -> float:
    """Mid-span centreline ``u3``, sampled by position rather than by node id.

    The id depends on the mesher; the position is the model's. The column is picked by component
    name, not position: ccx's ``DISP`` is ``[id, D1, D2, D3]``, Code_Aster's ``result__DEPL`` is
    ``[id, DX, DY, DZ, ...]``, and Sesam carries a derived displacement field whose first column is a
    magnitude, so position 3 there is Y.
    """
    from ada.fem.results.field_data import NodalFieldType

    if hasattr(res, "to_fea_result"):  # FEAResultV2 (Abaqus via the results SQLite) carries no mesh itself
        res = res.to_fea_result()
    coords = np.asarray(res.mesh.nodes.coords, dtype=float)
    # The last one: a reader that keeps the step's base-state frame (Abaqus' frame 0, all zeros) lists
    # it first, and the loaded increment is the step's final one.
    field = [
        f
        for f in res.results
        if getattr(f, "field_type", None) == NodalFieldType.DISP or f.name in ("DISP", "result__DEPL")
    ][-1]
    u3_col = next(i for i, c in enumerate(field.components) if c.upper() in ("U3", "D3", "DZ", "Z"))
    values = np.asarray(field.values, dtype=float)
    offset = np.abs(coords[:, 0] - PLATE_STRIP_LENGTH / 2) + np.abs(coords[:, 1] - PLATE_STRIP_WIDTH / 2)
    index = int(np.argmin(offset))
    assert offset[index] < 1e-06, "the mesh has no node at mid-span on the centreline"
    return float(values[index][u3_col + 1])


def _solve(a: ada.Assembly, fem_format: str, name: str) -> float:
    res = a.to_fem(name, fem_format, scratch_dir=SCRATCH_DIR, overwrite=True, execute=True, exit_on_complete=False)
    if res is None:
        # `to_fem` returns None under pytest for some formats; read the file back instead.
        from ada.fem.formats.utils import default_fem_res_path

        res = ada.from_fem_res(default_fem_res_path(name, scratch_dir=SCRATCH_DIR, fem_format=fem_format))
    return _mid_span_u3(res)


def test_the_mesh_this_rests_on_comes_from_a_plate_object():
    """Precondition, and the modelling route: a Plate meshed by the meshing module."""
    a = design_plate_strip()
    plate = a.get_part(PLATE_PART_NAME).plates[0]
    assert isinstance(plate, ada.Plate)
    assert a.get_part(PLATE_PART_NAME).fem.is_empty(), "no FEM before meshing; the Plate is the source"

    a = mesh_plate_strip(a, mesh_size=0.125, elem_order=1, use_quads=True)
    fem = a.get_part(PLATE_PART_NAME).fem

    from ada.fem.shapes.definitions import ShellShapes

    shells = [el for el in fem.elements if isinstance(el.type, ShellShapes)]
    assert len(shells) == 128, "4.0 x 0.5 at a 0.125 seed is 32 x 4 quads"
    assert all(el.type == ShellShapes.QUAD for el in shells)
    assert _element_normals_are_negative_z(a), "the winding the expected signs below depend on"


@pytest.mark.parametrize("fem_format", FORMATS)
def test_a_positive_pressure_pushes_into_the_face_it_names(fem_format):
    """The convention, per format. On this mesh the positive face looks down, so the strip goes up."""
    if not _solver_available(fem_format):
        pytest.skip(f"{fem_format} is not installed")

    u3 = _solve(_strip(MESH_SIZES[0]), fem_format, f"xfmt_pos_{fem_format}")

    assert u3 > 0.0, (
        f"{fem_format}: a positive pressure on the positive face must push into that face. This "
        f"mesh's normals are -z, so that is +z; got u3 = {u3!r}"
    )
    assert u3 == pytest.approx(plate_closed_form_deflection(), rel=2e-03), (
        f"{fem_format}: mid-span deflection at the coarsest seed should be within a fraction of a "
        f"percent of 5 q L^4 / (384 D)"
    )


@pytest.mark.parametrize("fem_format", FORMATS)
def test_the_other_face_pushes_the_other_way(fem_format):
    """Equal and opposite, not merely different -- the two faces of one shell."""
    if not _solver_available(fem_format):
        pytest.skip(f"{fem_format} is not installed")

    positive = _solve(_strip(MESH_SIZES[0]), fem_format, f"xfmt_face_pos_{fem_format}")
    negative = _solve(_strip(MESH_SIZES[0], negative_face=True), fem_format, f"xfmt_face_neg_{fem_format}")

    assert negative == pytest.approx(-positive, rel=1e-09), f"{fem_format}: {negative!r} vs -{positive!r}"


@pytest.mark.parametrize("fem_format", FORMATS)
def test_the_deflection_converges_onto_the_closed_form(fem_format):
    """Second order on a halved mesh, extrapolated onto ``5 q L^4 / (384 D)``.

    Three seeds rather than one, because a single mesh agreeing with a closed form to a fraction of a
    percent is weaker evidence than a sequence approaching it at the right rate: the rate is what
    says the residual is discretisation and not a modelling error that happens to be small.
    """
    if not _solver_available(fem_format):
        pytest.skip(f"{fem_format} is not installed")

    measured = [_solve(_strip(h), fem_format, f"xfmt_conv_{fem_format}_{str(h).replace('.', 'p')}") for h in MESH_SIZES]
    coarse, medium, fine = measured

    ratio = (medium - coarse) / (fine - medium)
    assert ratio == pytest.approx(4.0, abs=0.15), f"{fem_format}: second order on a halved mesh, got {ratio}"

    richardson = fine + (fine - medium) / (ratio - 1.0)
    assert richardson == pytest.approx(plate_closed_form_deflection(), rel=1e-04), (
        f"{fem_format}: the extrapolant is {richardson!r} against a closed form of "
        f"{plate_closed_form_deflection()!r}"
    )


def test_the_free_solvers_agree_with_each_other():
    """Calculix and Code_Aster on the same deck-written model, to the same number.

    This is the comparison the report is built on, and it is stronger than either against the closed
    form alone: two independent element formulations and two independent load implementations
    reaching the same answer is what makes a third one's disagreement a finding rather than a
    tolerance question.
    """
    available = [f for f in ("calculix", "code_aster") if _solver_available(f)]
    if len(available) < 2:
        pytest.skip(f"needs both free solvers; have {available}")

    results = {f: _solve(_strip(MESH_SIZES[0]), f, f"xfmt_agree_{f}") for f in available}

    reference = plate_closed_form_deflection()
    for fem_format, u3 in results.items():
        assert u3 == pytest.approx(reference, rel=2e-03), f"{fem_format} is off the closed form: {u3!r}"

    values = list(results.values())
    assert values[0] == pytest.approx(values[1], rel=2e-03), f"the two solvers disagree: {results}"

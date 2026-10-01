"""Reduced integration in Code_Aster is a modelisation, not a cell type.

Solids sub-integrate through ``MODELISATION='3D_SI'`` (U3.14.01: HEXA8 by the assumed-strain method,
HEXA20 and TETRA10 by reduced Gauss integration). The plate/shell and beam modelisations have no
reduced variant, so asking for one is refused rather than silently ignored.
"""

import re

import pytest

import ada
from ada.api.fem_tasks import design_cantilever, is_eig_skip, mesh_cantilever
from ada.fem.exceptions import IncompatibleElements

_MODELISATION = re.compile(r"MODELISATION='([A-Z0-9_]+)'")


def _write_comm(tmp_path, *, geom_repr, elem_order, use_hex_quad, reduced_integration) -> str:
    a = mesh_cantilever(
        design_cantilever(),
        geom_repr=geom_repr,
        elem_order=elem_order,
        use_hex_quad=use_hex_quad,
        reduced_integration=reduced_integration,
        mesh_size=0.2,
    )
    a.fem.add_step(ada.fem.StepEigen("eig", num_eigen_modes=3))
    name = f"ri_{geom_repr}_o{elem_order}_hq{use_hex_quad}_ri{reduced_integration}"
    a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.comm").read_text()


@pytest.mark.parametrize("elem_order", [1, 2])
def test_reduced_integration_solids_use_3d_si(tmp_path, elem_order):
    comm = _write_comm(tmp_path, geom_repr="solid", elem_order=elem_order, use_hex_quad=True, reduced_integration=True)
    assert _MODELISATION.findall(comm) == ["3D_SI"]


def test_full_integration_solids_keep_3d(tmp_path):
    comm = _write_comm(tmp_path, geom_repr="solid", elem_order=1, use_hex_quad=True, reduced_integration=False)
    assert _MODELISATION.findall(comm) == ["3D"]


def test_reduced_integration_shells_are_refused(tmp_path):
    with pytest.raises(IncompatibleElements, match="only available for solids"):
        _write_comm(tmp_path, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=True)


def test_reduced_integration_linear_tetrahedra_are_refused(tmp_path):
    # 3D_SI has no TETRA4; only the quadratic TETRA10 sub-integrates.
    with pytest.raises(IncompatibleElements, match="3D_SI"):
        _write_comm(tmp_path, geom_repr="solid", elem_order=1, use_hex_quad=False, reduced_integration=True)


@pytest.mark.parametrize(
    "geom_repr, skipped",
    [("solid", False), ("shell", True), ("line", True)],
)
def test_verification_matrix_runs_code_aster_reduced_integration_for_solids_only(geom_repr, skipped):
    assert (
        is_eig_skip(
            fem_format="code_aster",
            geom_repr=geom_repr,
            elem_order=1,
            use_hex_quad=geom_repr != "line",
            reduced_integration=True,
        )
        is skipped
    )

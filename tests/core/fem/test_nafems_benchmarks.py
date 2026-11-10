"""The NAFEMS LE1 and LE10 models and the cantilever closed form, without a solver. tests/fem/test_nafems_le.py solves them."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from ada.api.fem_benchmarks import (
    LE1,
    LE10,
    NAFEMS_PART_NAME,
    NAFEMS_SURFACE_NAME,
    cantilever_bending_frequencies,
    design_nafems_le,
    mesh_nafems_le,
    nafems_case_name,
    stress_yy_at,
    stress_yy_nodes,
    stress_yy_on_plane,
)
from ada.api.fem_tasks import design_cantilever
from ada.fem.results.field_data import ElementFieldData, FieldPosition, NodalFieldData


def fem_of(a):
    return a.get_part(NAFEMS_PART_NAME).fem


@pytest.mark.parametrize("case", ["LE1", "LE10"])
def test_every_node_lies_between_the_two_ellipses(case):
    fem = fem_of(mesh_nafems_le(case, 2))
    for node in fem.nodes:
        inner = (node.x / 2) ** 2 + node.y**2
        outer = (node.x / 3.25) ** 2 + (node.y / 2.75) ** 2
        assert inner >= 1 - 1e-9 and outer <= 1 + 1e-9 and node.x >= -1e-9 and node.y >= -1e-9


@pytest.mark.parametrize(
    "case, divisions, elements", [("LE1", 2, 8), ("LE1", 4, 32), ("LE10", 2, 16), ("LE10", 4, 128)]
)
def test_element_counts(case, divisions, elements):
    a = mesh_nafems_le(case, divisions)
    assert len(fem_of(a).elements) == elements and a.metadata["nafems"]["n_elements"] == elements


def test_point_d_is_where_the_benchmark_says():
    assert mesh_nafems_le("LE1", 2).metadata["nafems"]["point_d"] == pytest.approx((2.0, 0.0, 0.0))
    assert mesh_nafems_le("LE10", 2).metadata["nafems"]["point_d"] == pytest.approx((2.0, 0.0, 0.3))


def test_targets_are_the_published_ones():
    assert LE1.target_pa == pytest.approx(92.7e6) and LE10.target_pa == pytest.approx(-5.38e6)


def test_le1_is_pulled_outward_and_le10_pushed_down():
    assert LE1.pressure < 0 < LE10.pressure


@pytest.mark.parametrize("case", ["LE1", "LE10"])
def test_no_dof_is_held_twice(case):
    """Code_Aster stops at <ASSEMBLA_26> if one is."""
    fem = fem_of(mesh_nafems_le(case, 4))
    seen = set()
    for bc in fem.bcs:
        for node in bc.fem_set.members:
            for dof in bc.dofs:
                assert (node.id, dof) not in seen
                seen.add((node.id, dof))


def test_le10_outer_face_restraints():
    fem = fem_of(mesh_nafems_le("LE10", 2))
    held: dict[int, set] = {}
    for bc in fem.bcs:
        for node in bc.fem_set.members:
            held.setdefault(node.id, set()).update(bc.dofs)
    outer = [n for n in fem.nodes if abs((n.x / 3.25) ** 2 + (n.y / 2.75) ** 2 - 1) < 1e-9]
    assert outer and all({1, 2} <= held[n.id] for n in outer)
    assert {3 in held[n.id] for n in outer if abs(n.z) < 1e-9} == {True}
    assert not any(3 in held[n.id] for n in outer if abs(n.z) > 1e-9)


def test_le1_has_a_free_outer_thickness():
    fem = fem_of(mesh_nafems_le("LE1", 2))
    held = {n.id for bc in fem.bcs if 3 in bc.dofs for n in bc.fem_set.members}
    assert held == {n.id for n in fem.nodes if abs(n.z) < 1e-9}


@pytest.mark.parametrize("case, faces", [("LE1", 4), ("LE10", 8)])
def test_the_loaded_surface_covers_one_face_per_element(case, faces):
    surface = fem_of(mesh_nafems_le(case, 2)).surfaces[NAFEMS_SURFACE_NAME]
    assert len(surface.fem_set) == faces and len(surface.el_face_index) == faces


def test_case_names():
    assert nafems_case_name("calculix", "LE10", 4) == "nafems_LE10_ccx_n4"


def _mesh(refs, as_indices=False):
    return SimpleNamespace(
        nodes=SimpleNamespace(
            coords=np.array([[0.0, 0, 0], [2.0, 0, 0], [3.0, 0, 0]]), identifiers=np.array([10, 20, 30])
        ),
        elements=[SimpleNamespace(node_refs=np.array(refs), node_refs_are_indices=as_indices)],
    )


def test_stress_from_a_nodal_field_is_found_by_position():
    field = NodalFieldData("STRESS", 1, ["SXX", "SYY"], np.array([[10, 1.0, 2.0], [20, 3.0, 4.0], [30, 5.0, 6.0]]))
    assert stress_yy_at(SimpleNamespace(mesh=_mesh([[10]]), results=[field]), (2, 0, 0)) == 4.0


def test_element_node_stresses_are_averaged_over_the_elements_at_the_node():
    values = np.array([[[0.0, 1.0], [0.0, 3.0]], [[0.0, 5.0], [0.0, 9.0]]])
    field = ElementFieldData("result__SIGM_ELNO", 0, ["SIXX", "SIYY"], values)
    result = SimpleNamespace(mesh=_mesh([[10, 20], [20, 30]]), results=[field])
    assert stress_yy_at(result, (2, 0, 0)) == pytest.approx((3.0 + 5.0) / 2)


def test_element_nodal_rows_are_averaged_over_the_elements_at_the_node():
    """Abaqus' ``position=NODES`` output: rows of (element, place in its connectivity, components)."""
    values = np.array([[1, 1, 0.0, 1.0], [1, 2, 0.0, 3.0], [2, 1, 0.0, 5.0], [2, 2, 0.0, 9.0]])
    field = ElementFieldData("S (element_nodal)", 1, ["S11", "S22"], values, field_pos=FieldPosition.ELEMENT_NODAL)
    mesh = _mesh([[10, 20], [20, 30]])
    mesh.elements[0].identifiers = np.array([1, 2])
    assert stress_yy_at(SimpleNamespace(mesh=mesh, results=[field]), (2, 0, 0)) == pytest.approx((3.0 + 5.0) / 2)


def test_integration_point_stresses_are_refused():
    field = ElementFieldData("result__SIEF_ELGA", 0, ["SIXX", "SIYY"], np.zeros((2, 8, 2)))
    with pytest.raises(ValueError, match="sigma_yy"):
        stress_yy_at(SimpleNamespace(mesh=_mesh([[10, 20], [20, 30]]), results=[field]), (2, 0, 0))


def test_a_point_with_no_node_is_an_error():
    field = NodalFieldData("STRESS", 1, ["SYY"], np.zeros((3, 2)))
    with pytest.raises(ValueError, match="no node"):
        stress_yy_at(SimpleNamespace(mesh=_mesh([[10]]), results=[field]), (2.5, 0, 0))


def test_cantilever_closed_form_is_the_euler_bernoulli_formula():
    """Against the section adapy itself computes, which is what the solvers receive."""
    beam = next(iter(design_cantilever().get_part("MyPart").get_all_physical_objects()))
    section, steel = beam.section.properties, beam.material.model

    def first(inertia: float) -> float:
        return 1.875104**2 / (2 * math.pi * 9) * math.sqrt(steel.E * inertia / (steel.rho * section.Ax))

    result = cantilever_bending_frequencies()
    assert result["Y"][0] == pytest.approx(first(section.Iz)) and result["Z"][0] == pytest.approx(first(section.Iy))
    assert result["Y"][1] / result["Y"][0] == pytest.approx((4.694091 / 1.875104) ** 2)
    assert len(result["Y"]) == 2 and result["Y"][0] < result["Z"][0]


def _mode(f_hz, efx=0.0, efy=0.0, efz=0.0):
    return SimpleNamespace(f_hz=f_hz, efx=efx, efy=efy, efz=efz)


@pytest.fixture
def report_utils():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "verification" / "utils.py"
    if not path.is_file():
        pytest.skip("verification/ is not shipped with the installed package's tests")
    spec = importlib.util.spec_from_file_location("verification_utils", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses looks the module up while the class is being built
    spec.loader.exec_module(module)
    return module


def test_bending_modes_are_matched_by_shape_not_by_index(report_utils):
    torsion = _mode(10.0)
    modes = [torsion, _mode(12.0, efy=120.0), _mode(50.0, efz=118.0), _mode(79.0, efy=36.0), _mode(300.0, efz=30.0)]
    assert report_utils.bending_modes(modes, "Y", 2) == [12.0, 79.0]
    assert report_utils.bending_modes(modes, "Z", 2) == [50.0, 300.0]


def test_a_solver_without_effective_masses_gives_no_bending_modes(report_utils):
    assert report_utils.bending_modes([SimpleNamespace(f_hz=1.0, efx=None, efy=None, efz=None)], "Y", 2) == []


def test_nafems_table_has_each_solver_with_its_deviation(report_utils):
    results = [
        SimpleNamespace(case="LE1", divisions=4, n_elements=32, stress_yy=90e6, fem_format="calculix"),
        SimpleNamespace(case="LE1", divisions=2, n_elements=8, stress_yy=88e6, fem_format="code_aster"),
    ]
    df = report_utils.create_nafems_df(results, 100e6)
    assert list(df["Divisions"]) == [2, 4]
    row = df.iloc[1]
    assert row["ccx [MPa]"] == 90.0 and row["ccx Δ [%]"] == -10.0 and row["target [MPa]"] == 100.0


def test_bending_table_drops_modes_nobody_captured_and_marks_gaps(report_utils):
    captured = [_mode(12.0, efy=120.0), _mode(50.0, efz=118.0), _mode(79.0, efy=36.0)]
    partial = [_mode(12.5, efy=120.0), _mode(51.0, efz=118.0)]
    results = [
        SimpleNamespace(
            metadata={"geo": "line", "elo": 2, "hexquad": False}, eig_data=SimpleNamespace(modes=m), fem_format=soft
        )
        for m, soft in ((captured, "abaqus"), (partial, "sesam"))
    ]
    df = report_utils.create_bending_deviation_df(results, "line", 2, {"Y": [12.0, 80.0], "Z": [50.0, 330.0]})
    assert list(df["Mode"]) == ["Y1", "Y2", "Z1"]  # no solver has Z2
    assert df.set_index("Mode").loc["Y2", "ses"] == "–" and df.set_index("Mode").loc["Y1", "aba"] == 0.0


@pytest.mark.parametrize("case, thickness", [("LE1", 0.1), ("LE10", 0.6)])
def test_the_design_is_the_benchmarks_full_thickness_and_outline(case, thickness):
    plate = next(iter(design_nafems_le(case).get_part(NAFEMS_PART_NAME).get_all_physical_objects()))
    assert plate.t == pytest.approx(thickness)
    xs = [p.x for p in plate.poly.points2d] if hasattr(plate.poly, "points2d") else []
    assert xs and max(xs) == pytest.approx(3.25) and min(xs) == pytest.approx(0.0, abs=1e-9)


def test_nodal_stress_on_a_plane_keeps_only_that_plane():
    field = NodalFieldData("STRESS", 1, ["SYY"], np.array([[10, 1.0], [20, 2.0], [30, 3.0]]))
    mesh = _mesh([[10]])
    mesh.nodes.coords = np.array([[0.0, 0, 0.3], [2.0, 0, 0.0], [3.0, 1, 0.3]])
    assert stress_yy_on_plane(SimpleNamespace(mesh=mesh, results=[field]), 0.3) == {
        "x": [0.0, 3.0],
        "y": [0.0, 1.0],
        "syy": [1.0, 3.0],
    }


def test_element_stress_maps_through_indices_too():
    values = np.array([[[0.0, 1.0], [0.0, 3.0]], [[0.0, 5.0], [0.0, 9.0]]])
    field = ElementFieldData("result__SIGM_ELNO", 0, ["SIXX", "SIYY"], values)
    _, syy = stress_yy_nodes(SimpleNamespace(mesh=_mesh([[0, 1], [1, 2]], as_indices=True), results=[field]))
    assert syy.tolist() == [1.0, 4.0, 9.0]


def test_contour_has_a_panel_per_solver_and_leaves_the_hole_empty(report_utils):
    pytest.importorskip("scipy")
    pytest.importorskip("plotly")
    ang = np.linspace(0, np.pi / 2, 25)
    x = np.concatenate([s * np.cos(ang) for s in (2.0, 2.6, 3.25)])
    y = np.concatenate([s * np.sin(ang) for s in (1.0, 1.9, 2.75)])
    plane = {"x": x.tolist(), "y": y.tolist(), "syy": (x * 1e6).tolist()}
    results = [SimpleNamespace(plane=plane, stress_yy=92e6, fem_format=f) for f in ("calculix", "code_aster")]
    fig = report_utils.create_nafems_contour(results, "t")
    contours = [t for t in fig.data if t.type == "contour"]
    assert len(contours) == 2 and {a.text for a in fig.layout.annotations} == {"ccx", "ca"}
    z, xs, ys = np.asarray(contours[0].z, float), contours[0].x, contours[0].y
    assert np.isnan(z[0, 0])  # the centre of the ellipse, inside the hole
    assert np.isfinite(z[np.argmin(np.abs(ys - 0.5)), np.argmin(np.abs(xs - 2.5))])
    assert [t.text[0] for t in fig.data if t.type == "scatter"] == ["D: 92.00", "D: 92.00"]
    axis = fig.layout.coloraxis
    assert axis.cmin == -axis.cmax and contours[0].contours.start == axis.cmin  # white is zero


def test_contour_needs_data(report_utils):
    pytest.importorskip("scipy")
    pytest.importorskip("plotly")
    empty = SimpleNamespace(plane=None, stress_yy=None, fem_format="calculix")
    assert report_utils.create_nafems_contour([empty], "t") is None

"""A point mass on a solid-only node runs in Sestra, and a failed Sestra run raises.

Sestra's one-noded mass element (GMAS) is 6-dof whatever NDOF its MGMASS record declares. The writer gives a node
touched only by solids 3 dofs, and used to write a point mass there as GMAS with a 3x3 MGMASS; Sestra V11.3-01 then
aborts a static run with "Trying to add element matrix with 6 degrees of freedom to system matrix, which is more
than 3" and an eigenvalue run with "GmasWrapper: Illegal input to mass matrix routine". The mass now goes out as a
3-dof BNMASS on such a node.

And ``to_fem(..., execute=True)`` returned normally after both failures: Sestra exits with code 0 and leaves an
``R1.SIN`` behind. It now raises ``FEASolveFailed`` with SESTRA.MLG's error, which the last test shows by writing the
mass the old way.

Skips, rather than passes, where Sestra is not installed.
"""

from __future__ import annotations

import pytest

import ada
from ada.api.fem_tasks import PART_NAME, design_cantilever, mesh_cantilever
from ada.fem import FemSet, Mass
from ada.fem.exceptions.fea_execution import FEASolveFailed
from ada.fem.shapes.definitions import MassTypes


def _sestra_exe():
    from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

    try:
        return get_sestra_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_sestra_exe() is None, reason="Sestra is not installed")


def _solid_cantilever_with_tip_mass(analysis: str) -> ada.Assembly:
    a = mesh_cantilever(
        design_cantilever(), geom_repr="solid", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.1
    )
    fem = a.get_part(PART_NAME).fem
    tip = max(fem.nodes, key=lambda n: (n.x, n.z))
    fem.add_mass(Mass("tip_mass", fem.add_set(FemSet("mass_node", [tip], "nset")), 1000.0, MassTypes.MASS, parent=fem))
    if analysis == "static":
        a.fem.add_step(ada.fem.StepImplicitStatic("gravity", init_incr=1, total_time=1)).add_load(
            ada.fem.LoadGravity("grav", -9.81)
        )
    else:
        a.fem.add_step(ada.fem.StepEigen("eig", num_eigen_modes=5))
    return a


@pytest.mark.parametrize("analysis", ["static", "eigen"])
def test_point_mass_on_a_solid_node_runs(tmp_path, analysis):
    name = f"mass_{analysis}"
    _solid_cantilever_with_tip_mass(analysis).to_fem(name, "sesam", scratch_dir=tmp_path, execute=True, overwrite=True)

    run_dir = tmp_path / name
    assert "Normal exit from Sestra" in (run_dir / "SESTRA.LIS").read_text(errors="replace")
    deck = (run_dir / f"{name}T1.FEM").read_text()
    assert "BNMASS" in deck and "MGMASS" not in deck


def test_a_failed_sestra_run_raises(tmp_path, monkeypatch):
    """The mass written as a GMAS element on its 3-dof node -- what the writer did before -- makes Sestra fail."""
    from ada.fem.formats.sesam.write import write_point_elements

    monkeypatch.setattr(write_point_elements, "is_mass_element", lambda mass, ndofs=None: len(mass.members) == 1)

    with pytest.raises(FEASolveFailed, match="6 degrees of freedom") as err:
        _solid_cantilever_with_tip_mass("static").to_fem("old", "sesam", scratch_dir=tmp_path, execute=True)
    assert err.value.solver == "sestra"
    assert err.value.file == tmp_path / "old" / "SESTRA.MLG"

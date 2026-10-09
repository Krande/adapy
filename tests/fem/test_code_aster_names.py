"""Code_Aster solves a model whose load or support is named like one of the command file's own concepts,
or not like a Python name at all, exactly as it solves the same model with plain names.

Measured on main with Code_Aster 18.1.8 (this cantilever shell under gravity): a load named ``model``
rebound ``model = AFFE_MODELE`` and stopped the run at ``<SUPERVIS_4>``, a Bc named ``model`` likewise,
and a load named ``my grav`` stopped it at ``<F>_SYNTAX_ERROR``; adapy then reported only
``FileNotFoundError: FEM result file does not exist``.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

import ada
from ada.api.fem_tasks import design_cantilever, mesh_cantilever
from ada.fem.formats.general import FEATypes
from ada.fem.formats.utils import default_fem_res_path


def _displacements(tmp_path, name: str, load_name: str, bc_name: str | None) -> np.ndarray:
    a = design_cantilever()
    a = mesh_cantilever(a, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.2)
    if bc_name is not None:
        p = a.get_part("MyPart")
        for bc in p.fem.bcs + a.fem.bcs:
            bc.name = bc_name
    step = a.fem.add_step(ada.fem.StepImplicitStatic("gravity", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity(load_name, -9.81 * 80))
    a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True, exit_on_complete=False)
    res = ada.from_fem_res(default_fem_res_path(name, scratch_dir=tmp_path, fem_format=FEATypes.CODE_ASTER))
    (disp,) = [f for f in res.results if f.name in ("DISP", "result__DEPL")][-1:]
    return np.asarray(disp.values, dtype=float)


@pytest.fixture(scope="module")
def reference(tmp_path_factory, require_solver):
    require_solver("code_aster")
    return _displacements(tmp_path_factory.mktemp("names_ref"), "plain", "grav", None)


@pytest.mark.parametrize(
    "load_name, bc_name",
    [("model", None), ("my grav", None), ("grav", "model")],
    ids=["load_named_model", "load_named_my_grav", "bc_named_model"],
)
def test_a_name_the_command_file_cannot_bind_solves_as_a_plain_one(tmp_path, reference, load_name, bc_name):
    values = _displacements(tmp_path, "named", load_name, bc_name)
    assert np.abs(reference).max() > 0.0
    # The same deck but for its concept names: the same numbers, to the last digit.
    np.testing.assert_array_equal(values, reference)


def _coupled_cantilever(tmp_path, name: str, load_name: str, ref_pts_elset: bool) -> tuple[str, np.ndarray]:
    """The cantilever as shells, its root end held through a coupling to a reference node (``fix_end``), under
    gravity: the supports hold the reference node only, so the coupling has to be in every step's ``EXCIT`` or
    the shells are a mechanism."""
    from ada.api.fem_tasks import BEAM_NAME, PART_NAME
    from ada.base.types import GeomRepr

    a = design_cantilever()
    p = a.get_part(PART_NAME)
    bm = next(b for b in p.get_all_physical_objects() if b.name == BEAM_NAME)
    bm.concept_fem.fix_end("n1")
    p.fem = p.to_fem_obj(0.1, GeomRepr.SHELL)
    if ref_pts_elset:
        p.fem.add_set(ada.fem.FemSet("ref_pts", list(p.fem.elements)[:3], "elset"))
    step = a.fem.add_step(ada.fem.StepImplicitStatic("gravity", nl_geom=False))
    step.add_load(ada.fem.LoadGravity(load_name, -9.81))
    a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True, exit_on_complete=False)
    mess = (tmp_path / name / f"{name}.mess").read_text(encoding="utf-8", errors="replace")
    (verdict,) = [line for line in mess.splitlines() if "DIAGNOSTIC JOB" in line]
    assert "<S>" not in verdict and "<F>" not in verdict and "<E>" not in verdict, verdict
    comm = (tmp_path / name / f"{name}.comm").read_text(encoding="utf-8")
    res = ada.from_fem_res(default_fem_res_path(name, scratch_dir=tmp_path, fem_format=FEATypes.CODE_ASTER))
    (disp,) = [f for f in res.results if f.name in ("DISP", "result__DEPL")][-1:]
    return comm, np.asarray(disp.values, dtype=float)


def test_a_coupling_is_its_own_charge_beside_the_supports_and_a_load_named_supports(tmp_path, require_solver):
    """Every support is a row of the one ``supports`` charge; a coupling is a charge of its own, ``cp_<name>``,
    in each step beside it, and a load named ``supports`` is ``ld_supports``. Measured with Code_Aster 18.1.8:
    root reference node held, coupled to the root section, the cantilever deflects under gravity (without the
    coupling in EXCIT the shells are a mechanism)."""
    require_solver("code_aster")
    comm, values = _coupled_cantilever(tmp_path, "coupled", "supports", ref_pts_elset=False)
    (excit,) = re.findall(r"EXCIT=\((.*?)\)\n", comm)
    charges = re.findall(r"CHARGE=(\w+)", excit)
    assert charges[0] == "supports" and charges[-1] == "ld_supports", charges
    (coupling,) = charges[1:-1]
    assert coupling.startswith("cp_") and f"{coupling} = AFFE_CHAR_MECA(" in comm
    assert values[:, 1:4].min() < 0.0


def test_a_user_element_set_named_ref_pts_leaves_the_couplings_reference_points_a_group_of_their_own(
    tmp_path, require_solver
):
    """The couplings' reference points are created as an element group, ``ref_pts``. Measured with Code_Aster
    18.1.8: with a user element set of that name the run stopped at <MESH1_20> (the group "existe déjà"). Now the
    deck takes ``ref_pts_2``, and solves the model as it solves it without that set, to the last digit."""
    require_solver("code_aster")
    _, reference = _coupled_cantilever(tmp_path / "plain", "plain", "grav", ref_pts_elset=False)
    comm, values = _coupled_cantilever(tmp_path / "taken", "taken", "grav", ref_pts_elset=True)
    assert "NOM_GROUP_MA='ref_pts_2'" in comm and "GROUP_MA='ref_pts'" not in comm
    np.testing.assert_array_equal(values, reference)

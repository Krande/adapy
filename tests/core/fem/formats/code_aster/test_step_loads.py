"""Every load of a Code_Aster static step is applied, not just the first.

Both static writers put ``step.loads[0]`` alone in ``EXCIT``: the step's other loads were written to the
deck as ``AFFE_CHAR_MECA`` concepts and never applied, without a word.
"""

from __future__ import annotations

import re

import pytest

import ada
from ada.api.fem_tasks import design_cantilever, mesh_cantilever


def _deck(tmp_path, nl_geom: bool) -> str:
    a = design_cantilever()
    a = mesh_cantilever(a, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.2)
    step = a.fem.add_step(ada.fem.StepImplicitStatic("st", nl_geom=nl_geom, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("grav1", -9.81 * 80))
    step.add_load(ada.fem.LoadGravity("grav2", -9.81 * 20))
    a.to_fem("loads", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=False)
    return next(tmp_path.rglob("*.comm")).read_text()


@pytest.mark.parametrize("nl_geom", [False, True], ids=["linear", "nonlinear"])
def test_every_load_of_the_step_is_in_excit(tmp_path, nl_geom):
    comm = _deck(tmp_path, nl_geom)
    defined = set(re.findall(r"^(ld_\w+) = AFFE_CHAR_MECA", comm, re.M))
    (excit,) = re.findall(r"EXCIT=\((.*?)\)\s*[,)]\n", comm, re.S)
    applied = set(re.findall(r"CHARGE=(ld_\w+)", excit))
    assert defined == {"ld_grav1", "ld_grav2"}
    assert applied == defined
    if nl_geom:  # each load on the step's ramp
        assert excit.count("FONC_MULT=bc_step") == 2

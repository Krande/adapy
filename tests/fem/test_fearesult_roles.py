"""A results file as a format-neutral FEA result (``ada.fem.results.open_result``): the model data a
structural check reads and the role fields of every case, the same from a Sesam SIN and from Code_Aster."""

from __future__ import annotations

import numpy as np
import pytest
from test_code_aster_sin import (  # the model of the SIN conversion's tests
    LX,
    LY,
    F,
    T,
    _stiffened_plate,
)

from ada.fem.results import open_result
from ada.fem.results.field_data import FieldRole


def _by_role(result) -> dict:
    out: dict = {}
    for field in result.results:
        if field.role is not None:
            out.setdefault(field.role, []).append(field)
    return out


def test_a_sin_opens_with_its_model_data_and_role_fields(fem_files):
    res = open_result(fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN")
    mesh = res.mesh
    assert res.cases.stored and set(res.cases.names) >= set(res.cases.stored)
    shells = {int(e) for b in mesh.elements if b.elem_info.type.name in ("QUAD", "TRI") for e in b.identifiers}
    assert shells and set(mesh.thickness) == shells
    assert set(mesh.element_frames) >= shells and set(mesh.result_points) >= shells
    for frame in mesh.element_frames.values():
        assert frame @ frame.T == pytest.approx(np.eye(3), abs=1e-5)  # orthonormal axes
    step, case = next(iter(res.cases.iter_steps(res.cases.stored[:1])))
    roles = _by_role(case)
    assert FieldRole.NODAL_DISPLACEMENT in roles and FieldRole.SHELL_STRESS in roles
    stress = np.vstack([np.asarray(f.values) for f in roles[FieldRole.SHELL_STRESS]])
    assert stress.shape[1] == 5 and set(stress[:, 0].astype(int)) <= shells


def test_an_unknown_suffix_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no result opener"):
        open_result(tmp_path / "x.unknown")


def test_an_rmed_without_adapys_sidecar_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError, match="sidecar"):
        open_result(tmp_path / "x.rmed")


def test_code_aster_reads_alike_from_the_rmed_and_from_its_sin(tmp_path, require_solver):
    """The .rmed opened directly and through its SIN conversion (``options.CODE_ASTER.write_sin``) give the same
    model data and role fields: one convention, whichever door."""
    require_solver("code_aster")
    a = _stiffened_plate()
    a.fem.options.CODE_ASTER.write_sin = True
    a.to_fem("sp", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    direct = open_result(tmp_path / "sp" / "sp.rmed")
    via_sin = open_result(tmp_path / "sp" / "spR1.SIN")

    md, ms = direct.mesh, via_sin.mesh
    assert md.thickness == pytest.approx(ms.thickness)
    assert set(md.element_frames) == set(ms.element_frames)
    for e, frame in md.element_frames.items():
        assert frame == pytest.approx(ms.element_frames[e], abs=1e-5)
    assert direct.cases.names == via_sin.cases.names == {1: "pull"}

    ((_, rd),) = direct.cases.iter_steps([1])
    ((_, rs),) = via_sin.cases.iter_steps([1])
    fd, fs = _by_role(rd), _by_role(rs)
    for role in (FieldRole.SHELL_STRESS, FieldRole.BEAM_FORCES):
        d = {tuple(r[:2].astype(int)): r[2:] for f in fd[role] for r in np.asarray(f.values)}
        s = {tuple(r[:2].astype(int)): r[2:] for f in fs[role] for r in np.asarray(f.values)}
        assert set(d) == set(s), role
        scale = max(np.abs(v).max() for v in s.values())
        # The SIN stores float32.
        assert max(np.abs(d[k] - s[k]).max() for k in s) <= 1e-6 * scale, role
    # The stiffener carries its share of the pull, tension positive.
    nxx = np.array([r[2] for f in fd[FieldRole.BEAM_FORCES] for r in np.asarray(f.values)])
    assert 0 < nxx.mean() < F and LX > 0 and LY * T > 0

"""SIF byte-offset index → reduced single-step reads.

The index records each result step's RV* byte spans so a reader can be fed
only the bytes for one step (the file minus the other steps), cutting the
download + parse of a big multi-step deck. These tests pin: the build is
correct, ``include_ranges`` keeps everything that isn't another step,
the JSON sidecar round-trips, and — the load-bearing invariant — a reduced
file reads byte-identical to a full single-step read.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from ada.fem.formats.sesam.results.read_sif import read_sif_file
from ada.fem.formats.sesam.results.sif_index import (
    INDEX_VERSION,
    SifStepIndex,
    assemble_reduced_local,
    build_sif_index,
)

_EIGEN = "cantilever/sesam/eigen/shell/EIGEN_SHELL_CANTILEVER_SESAMR1.SIF"  # 20 steps
_ONE_STEP = "sesam/2EL_SHELL_R1.SIF"


def _disp(res):
    for r in res.results:
        if getattr(r, "name", None) == "RVNODDIS":
            return np.asarray(r.values)
    return None


def _reduced_read(tmp_path, sif, idx, step):
    ranges = idx.include_ranges(step)
    red = tmp_path / f"reduced_s{step}.SIF"
    assemble_reduced_local(sif, ranges, red)
    return read_sif_file(red), ranges


def test_build_index_steps_and_fields(fem_files):
    idx = build_sif_index(fem_files / _EIGEN)
    assert idx.steps == list(range(1, 21))
    assert set(idx.fields) == {"RVNODDIS", "RVSTRESS"}
    # 20 steps × 2 RV cards = 40 contiguous step spans.
    assert len(idx.step_spans) == 40
    assert idx.size == (fem_files / _EIGEN).stat().st_size


def test_include_ranges_excludes_other_steps(fem_files):
    idx = build_sif_index(fem_files / _EIGEN)
    ranges = idx.include_ranges(3)
    # Every step-3 span is fully covered; no step-≠3 span overlaps the ranges.
    def covered(a, b):
        return any(s <= a and b <= e for s, e in ranges)

    for ires, s, e in idx.step_spans:
        if ires == 3:
            assert covered(s, e), f"step-3 span {s},{e} not kept"
        else:
            assert not any(rs < e and s < re for rs, re in ranges), f"step-{ires} span leaked"


def test_reduced_read_matches_full_single_step(tmp_path, fem_files):
    sif = fem_files / _EIGEN
    idx = build_sif_index(sif)
    for step in (1, 3, 20):
        reduced, ranges = _reduced_read(tmp_path, sif, idx, step)
        full = read_sif_file(sif, step=step)
        assert reduced.get_steps() == [step] == full.get_steps()
        a, b = _disp(full), _disp(reduced)
        assert a is not None and b is not None
        assert a.shape == b.shape and np.allclose(a, b)
        # The reduced file is strictly smaller than the whole deck.
        assert sum(e - s for s, e in ranges) < idx.size


def test_reduced_mesh_matches(tmp_path, fem_files):
    sif = fem_files / _EIGEN
    idx = build_sif_index(sif)
    reduced, _ = _reduced_read(tmp_path, sif, idx, 5)
    full = read_sif_file(sif, step=5)
    assert np.allclose(reduced.mesh.nodes.coords, full.mesh.nodes.coords)


def test_json_round_trip(fem_files):
    idx = build_sif_index(fem_files / _EIGEN)
    again = SifStepIndex.from_json(idx.to_json())
    assert again.size == idx.size
    assert again.steps == idx.steps
    assert again.fields == idx.fields
    assert again.step_spans == idx.step_spans


def test_json_version_mismatch_rejected(fem_files):
    idx = build_sif_index(fem_files / _EIGEN)
    import json

    d = json.loads(idx.to_json())
    d["version"] = INDEX_VERSION + 99
    with pytest.raises(ValueError, match="unsupported SIF index version"):
        SifStepIndex.from_json(json.dumps(d))


def test_single_step_file(tmp_path, fem_files):
    sif = fem_files / _ONE_STEP
    idx = build_sif_index(sif)
    assert len(idx.steps) == 1
    step = idx.default_step()
    reduced, _ = _reduced_read(tmp_path, sif, idx, step)
    full = read_sif_file(sif)
    assert reduced.get_steps() == full.get_steps()
    assert np.allclose(_disp(reduced), _disp(full))


def test_default_step_is_first(fem_files):
    idx = build_sif_index(fem_files / _EIGEN)
    assert idx.default_step() == 1

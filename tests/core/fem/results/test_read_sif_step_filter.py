"""Single-step SIF reading bounds peak memory.

A SIF result deck holds every step's RV* records (RVNODDIS / RVSTRESS /
RVFORCES) in one contiguous block; the legacy reader materialised all of
them even though a GLB render only colours one (step, field). The
``step=`` filter — the SIF analogue of ``read_sin_file(step=...)`` — keeps
only the requested step, so an N-step deck costs ~1/N the RAM. These tests
pin the filter's correctness (values identical to the full read), the
``"first"`` sentinel, full back-compat, and that memory actually drops.
"""

from __future__ import annotations

import tracemalloc

import numpy as np

from ada.fem.formats.sesam.results.read_sif import read_sif_file

_EIGEN = "cantilever/sesam/eigen/shell/EIGEN_SHELL_CANTILEVER_SESAMR1.SIF"  # 20 steps
_ONE_STEP = "sesam/2EL_SHELL_R1.SIF"  # single step


def _disp(res, step):
    for r in res.results:
        if getattr(r, "name", None) == "RVNODDIS" and int(r.step) == step:
            return np.asarray(r.values)
    return None


def test_full_read_unchanged(fem_files):
    # step=None (the default every existing caller uses) loads every step.
    res = read_sif_file(fem_files / _EIGEN)
    assert res.get_steps() == list(range(1, 21))


def test_step_filter_loads_single_step(fem_files):
    res = read_sif_file(fem_files / _EIGEN, step=3)
    assert res.get_steps() == [3]
    # Both fields present in that deck filter down to the one step.
    grouped = {k: sorted(int(d.step) for d in v) for k, v in res.get_results_grouped_by_field_value().items()}
    assert grouped == {"RVNODDIS": [3], "STRESS": [3]}


def test_step_first_sentinel_loads_first_step(fem_files):
    res = read_sif_file(fem_files / _EIGEN, step="first")
    assert res.get_steps() == [1]


def test_single_step_values_match_full(fem_files):
    # The filtered read must be byte-for-byte the same data the full read
    # produces for that step — only the *other* steps are dropped.
    full = read_sif_file(fem_files / _EIGEN)
    s3 = read_sif_file(fem_files / _EIGEN, step=3)
    a, b = _disp(full, 3), _disp(s3, 3)
    assert a is not None and b is not None
    assert a.shape == b.shape
    assert np.allclose(a, b)
    # Mesh is step-invariant and must be identical.
    assert full.mesh.nodes.coords.shape == s3.mesh.nodes.coords.shape
    assert np.allclose(full.mesh.nodes.coords, s3.mesh.nodes.coords)


def test_first_equals_full_step_one(fem_files):
    full = read_sif_file(fem_files / _EIGEN)
    first = read_sif_file(fem_files / _EIGEN, step="first")
    assert np.allclose(_disp(full, 1), _disp(first, 1))


def test_one_step_file_unaffected(fem_files):
    # A single-step deck must read identically whether or not the filter runs.
    full = read_sif_file(fem_files / _ONE_STEP)
    first = read_sif_file(fem_files / _ONE_STEP, step="first")
    assert full.get_steps() == first.get_steps()
    s = full.get_steps()[0]
    assert np.allclose(_disp(full, s), _disp(first, s))


def test_step_filter_bounds_memory(fem_files):
    # Peak allocation for one step must be well below the full multi-step read.
    f = fem_files / _EIGEN

    tracemalloc.start()
    read_sif_file(f)
    _, peak_full = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    tracemalloc.start()
    read_sif_file(f, step="first")
    _, peak_first = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 20 steps → single step should be a large fraction smaller. Assert a
    # conservative half to stay robust against allocator noise.
    assert peak_first < 0.6 * peak_full, f"peak_first={peak_first} peak_full={peak_full}"


def test_missing_step_yields_no_field(fem_files):
    # Asking for a step that isn't in the deck yields a result with no field
    # data for it (the converter surfaces that as a clean error upstream).
    res = read_sif_file(fem_files / _EIGEN, step=999)
    assert res.get_steps() == []

"""Parity test for the new h5py-based RMED reader.

Compares :func:`med_to_mesh_data` against ``meshio.read(path,
"med")`` across the full RMED test corpus. Once meshio drops out
of `feature.prod` (Stage C), this test is deleted — until then,
it's the truth source that locks in the parity invariant.
"""

from __future__ import annotations

import numpy as np
import pytest

from ada.fem.formats.code_aster.read.med_reader import med_to_mesh_data

# All RMED fixtures we have on disk. Covers the cell-type matrix
# (SEG2, QU4, TR3, TE4, HE8, ...) plus the eigen vs static
# split (multi-step vs single-step field naming).
RMED_FIXTURES = [
    "code_aster/Cantilever_CA_EIG_bm.rmed",
    "code_aster/Cantilever_CA_EIG_sh.rmed",
    "cantilever/code_aster/static_shell_cantilever_code_aster.rmed",
    "cantilever/code_aster/static_solid_cantilever_code_aster.rmed",
    "cantilever/code_aster/static_line_cantilever_code_aster.rmed",
    "cantilever/code_aster/eigen_shell_cantilever_code_aster.rmed",
]


@pytest.mark.parametrize("rmed_rel", RMED_FIXTURES)
def test_h5py_reader_matches_meshio(fem_files, rmed_rel):
    meshio = pytest.importorskip("meshio")

    rmed = fem_files / rmed_rel
    if not rmed.exists():
        pytest.skip(f"fixture not present: {rmed_rel}")

    got = med_to_mesh_data(rmed)
    ref = meshio.read(rmed, "med")

    np.testing.assert_array_equal(got.points, ref.points)

    assert len(got.cells) == len(ref.cells)
    for g_cb, r_cb in zip(got.cells, ref.cells):
        assert g_cb.type == r_cb.type
        np.testing.assert_array_equal(g_cb.data, r_cb.data)

    assert sorted(got.point_data) == sorted(ref.point_data)
    for k, v in got.point_data.items():
        # equal_nan because profile-restricted fields legitimately
        # back-fill with NaN (matches meshio's behaviour).
        np.testing.assert_array_equal(v, ref.point_data[k])

    assert sorted(got.cell_data) == sorted(ref.cell_data)
    for k, blocks in got.cell_data.items():
        ref_blocks = ref.cell_data[k]
        assert len(blocks) == len(ref_blocks)
        for g_block, r_block in zip(blocks, ref_blocks):
            np.testing.assert_array_equal(g_block, r_block)

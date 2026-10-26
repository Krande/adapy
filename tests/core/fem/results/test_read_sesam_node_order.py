"""The SIF results mesh comes out in adapy's node order, not Sesam's.

Sesam numbers SCQS (the 8-node shell) round the perimeter. Passed through unchanged, an 8-node
shell triangulates into slivers in the viewer and its posters. BTSS (the 3-node beam) is written
(end, mid, end) -- adapy's own LINE3 order -- and must pass through untouched: swapped, a curved
beam draws from one end to its middle.
"""

import numpy as np
import pytest

from ada.fem.formats.sesam.results.read_sif import Sif2Mesh, SifReader


@pytest.fixture
def one_elem_sif(fem_files):
    with open(fem_files / "sesam/1EL_SHELL_R1.SIF") as f:
        sif = SifReader(f)
        sif.load()
    return sif


def _with_element(sif, eltype: int, coords: list[tuple[float, float, float]]):
    ids = np.arange(1, len(coords) + 1, dtype=float)
    sif.nodes = np.column_stack([ids, np.asarray(coords, dtype=float)])
    sif.node_ids = np.column_stack([ids, ids])
    sif.elements = [(float(eltype), 1.0, ids.tolist())]
    return Sif2Mesh(sif).get_sif_mesh()


def test_scqs_corners_first(one_elem_sif):
    # Unit square, Sesam order: corner, mid-side, corner, ... round the perimeter.
    perimeter = [(0, 0, 0), (0.5, 0, 0), (1, 0, 0), (1, 0.5, 0), (1, 1, 0), (0.5, 1, 0), (0, 1, 0), (0, 0.5, 0)]
    mesh = _with_element(one_elem_sif, 28, perimeter)

    (block,) = mesh.elements
    assert block.node_refs.tolist() == [[1, 3, 5, 7, 2, 4, 6, 8]]


def test_btss_passes_through_end_mid_end(one_elem_sif):
    # As Sestra writes it: end, mid, end.
    mesh = _with_element(one_elem_sif, 23, [(0, 0, 0), (0.5, 0, 0), (1, 0, 0)])

    (block,) = mesh.elements
    assert block.node_refs.tolist() == [[1, 2, 3]]

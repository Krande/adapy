"""Regression coverage for 2nd-order element node-count resolution.

Code Aster (and some Calculix stacks) emit central-node element
variants — TRI7 / QUAD9 for shell-O2, PYRAMID13 for solids. The
visualization edge/face maps already carry corner-only entries for
these (see ``shapes/shells.py`` + ``shapes/solids.py``), but
``ShapeResolver.NUM_MAP`` was missing the 7 / 9 / 13 node counts, so
``get_el_nodes_from_type`` raised ``"... is not yet supported"`` deep
in ``_compute_topology`` — which ``bake_fea_bundles`` swallowed,
leaving those FEA cases without artefacts/posters in the docs.
"""

import pytest

from ada.fem.shapes.definitions import (
    ElemShape,
    ShapeResolver,
    ShellShapes,
    SolidShapes,
)


@pytest.mark.parametrize(
    "el_type, n_nodes",
    [
        (ShellShapes.TRI7, 7),
        (ShellShapes.QUAD9, 9),
        (SolidShapes.PYRAMID13, 13),
    ],
)
def test_second_order_node_counts_resolve(el_type, n_nodes):
    assert ShapeResolver.get_el_nodes_from_type(el_type) == n_nodes


@pytest.mark.parametrize(
    "el_type, n_nodes, n_corner_faces",
    [
        (ShellShapes.TRI7, 7, 1),   # one corner triangle
        (ShellShapes.QUAD9, 9, 2),  # quad split into two triangles
    ],
)
def test_o2_shell_elemshape_builds_corner_faces(el_type, n_nodes, n_corner_faces):
    # ElemShape validates the node count, then triangulates the corners
    # (mid-edge + central nodes ignored for viz).
    shape = ElemShape(el_type, list(range(n_nodes)))
    faces = shape.get_faces()
    assert len(faces) == 3 * n_corner_faces
    # Only the corner indices are referenced — the central node (and the
    # mid-edge nodes) are dropped for the straight-edge viz mesh.
    assert max(faces) <= 3

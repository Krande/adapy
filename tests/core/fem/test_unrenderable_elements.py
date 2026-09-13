"""Element types the renderer has no geometry for must not abort a conversion.

A 400 MB Sesam model used to fail its GLB conversion outright on
``ValueError: Element type SpringTypes.SPRING1 is currently not supported for
Visualization`` — one unsupported element type cost the user the whole model. The
walks now leave those elements out of the scene and say so in the log.

The mesh here is built in memory rather than read from a fixture file: the defect
is in the walk over element blocks, and a hand-built two-block mesh pins exactly
that without dragging a reader in with it.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from ada.config import logger
from ada.fem.formats.general import FEATypes
from ada.fem.results.common import ElementBlock, ElementInfo, FemNodes, Mesh
from ada.fem.shapes import definitions as shape_def
from ada.visit.gltf.graph import GraphNode, GraphStore

# A unit quad (nodes 1-4) plus one extra node (5) for the point-like elements to sit on.
_COORDS = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0], [2.0, 0.0, 0.0]])
_IDENTIFIERS = np.array([1, 2, 3, 4, 5])


def _block(el_type, node_refs, identifiers) -> ElementBlock:
    return ElementBlock(
        elem_info=ElementInfo(type=el_type, source_software=FEATypes.SESAM, source_type=str(el_type)),
        node_refs=np.array(node_refs),
        identifiers=np.array(identifiers),
    )


def _mesh(*extra_blocks: ElementBlock) -> Mesh:
    quad = _block(shape_def.ShellShapes.QUAD, [[1, 2, 3, 4]], [10])
    return Mesh(elements=[quad, *extra_blocks], nodes=FemNodes(coords=_COORDS, identifiers=_IDENTIFIERS))


@pytest.mark.parametrize(
    "el_type, node_refs",
    [
        # The type in the reported traceback: a spring to ground, one node, no second
        # end to draw a line to.
        (shape_def.SpringTypes.SPRING1, [[5]]),
        (shape_def.MassTypes.MASS, [[5]]),
        (shape_def.MassTypes.ROTARYI, [[5]]),
        (shape_def.MassTypes.NONSTRUCTURAL, [[5]]),
    ],
)
def test_unrenderable_block_is_skipped_not_fatal(el_type, node_refs):
    mesh = _mesh(_block(el_type, node_refs, [20]))

    edges, faces = mesh.get_edges_and_faces_from_mesh()

    # The quad survives intact: 4 edges, and 2 triangles from the one quad.
    assert len(faces) == 2
    assert len(edges) == 4


def test_spring2_still_renders_as_an_edge():
    """SPRING2 has two ends, so it draws as a line — the skip must not swallow it.

    It is also not a LineShape, which is how it used to reach the face tables and
    fail there with an AttributeError instead of being treated as one-dimensional.
    """
    mesh = _mesh(_block(shape_def.SpringTypes.SPRING2, [[1, 5]], [20]))

    edges, faces = mesh.get_edges_and_faces_from_mesh()

    assert len(faces) == 2
    assert len(edges) == 5  # 4 from the quad, 1 from the spring


def test_the_skip_is_reported(monkeypatch, caplog):
    # `ada.config` sets propagate = False on the `ada` logger, so caplog's root
    # handler never sees the record unless propagation is re-enabled for the test.
    monkeypatch.setattr(logger, "propagate", True)
    mesh = _mesh(_block(shape_def.SpringTypes.SPRING1, [[5], [5]], [20, 21]))

    with caplog.at_level(logging.WARNING, logger=logger.name):
        mesh.get_edges_and_faces_from_mesh()

    # Silence would leave the user with a model that merely looks wrong.
    assert "SpringTypes.SPRING1=2" in caplog.text


def test_mesh_stores_walk_skips_the_same_blocks():
    """The scene path (Part.to_gltf / FEM.show) walks blocks separately."""
    mesh = _mesh(_block(shape_def.SpringTypes.SPRING1, [[5]], [20]))
    root = GraphNode("root", 0)
    graph = GraphStore(root, {0: root})

    ms = mesh.create_mesh_stores("m", graph, root)

    # Flat index buffers: 2 triangles and the quad's 4 edges.
    assert len(ms.faces.indices) == 6
    assert len(ms.lines.indices) == 8


def test_renderability_is_read_off_the_edge_tables():
    """`is_renderable` must stay derived, so a newly mapped type needs no second edit."""
    from ada.fem.shapes.lines import line_edges
    from ada.fem.shapes.shells import shell_edges
    from ada.fem.shapes.solids import solid_edges

    for el_type in line_edges | shell_edges | solid_edges:
        assert shape_def.is_renderable(el_type)

    assert not shape_def.is_renderable(shape_def.SpringTypes.SPRING1)
    assert not shape_def.is_renderable(shape_def.MassTypes.MASS)

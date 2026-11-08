"""Feature edges for offscreen CAD images: the viewer's rule (boundary + dihedral > 30 degrees)."""

import numpy as np
import trimesh

from ada.visit.rendering.feature_edges import feature_edge_pairs, glb_with_feature_edges


def _soup(mesh):
    """A triangle soup, as a tessellated GLB is: every triangle owns its own vertices."""
    v = mesh.vertices[mesh.faces].reshape(-1, 3)
    return v, np.arange(len(v)).reshape(-1, 3)


def test_a_box_has_its_twelve_edges_and_no_face_diagonals():
    v, f = _soup(trimesh.creation.box())
    _, pairs = feature_edge_pairs(v, f)
    assert len(pairs) == 12


def test_a_flat_quad_has_its_outline_only():
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    _, pairs = feature_edge_pairs(v, np.array([[0, 1, 2], [0, 2, 3]]))
    assert len(pairs) == 4


def test_a_cylinder_shows_its_rims_not_its_facets():
    v, f = _soup(trimesh.creation.cylinder(radius=1.0, height=2.0, sections=64))
    w, pairs = feature_edge_pairs(v, f)
    z = w[pairs][:, :, 2]
    assert np.all(np.isclose(z[:, 0], z[:, 1])), "only rim edges: none along the side"
    assert len(pairs) == 2 * 64


def test_the_edges_ride_along_in_the_glb_as_uncoloured_lines(tmp_path):
    src, dest = tmp_path / "box.glb", tmp_path / "edges.glb"
    trimesh.creation.box().export(src)
    glb_with_feature_edges(src, dest)
    scene = trimesh.load(dest, force="scene")
    lines = [g for g in scene.geometry.values() if isinstance(g, trimesh.path.Path3D)]
    # read back as GL_LINES: two vertices per segment
    assert len(lines) == 1 and len(lines[0].vertices) == 2 * 12

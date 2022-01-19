from ada import Beam
from ada.visualize.new_render_api import Visualize
from ada.visualize.renderer_occ import occ_shape_to_faces
import pytest


@pytest.fixture
def beam():
    return Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE300")


def test_viz_beam(test_dir, beam):
    viz = Visualize()
    viz.add_obj(beam)
    # viz.display(off_screen_file=self.test_folder / "MyTest.svg")


def test_viz_beam_manual(test_dir, beam):
    geom = beam.solid
    quality = 1.0
    render_edges = False
    parallel = True
    np_vertices, np_faces, np_normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)


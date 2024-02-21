import pytest

from ada import Beam
from ada.occ.tessellating import tessellate_shape


@pytest.fixture
def beam():
    return Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE300")


def test_viz_beam_manual(test_dir, beam):
    geom = beam.solid_occ()
    quality = 1.0
    render_edges = False
    parallel = True
    _ = tessellate_shape(geom, quality, render_edges, parallel)

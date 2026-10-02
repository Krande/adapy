"""Static posters draw beams as solids, deformed the way the viewer deforms them.

A beam drawn as a line cannot show a torsion mode. The poster renderer now reads the bundle's
beam solids and warps each vertex by ``lerp(u) + lerp(θ) × r`` -- the viewer's formula -- so a
twisting mode twists in the PDF too.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from ada.fem.results.artefacts.beam_solids import write_beam_solids_warp
from ada.fem.results.artefacts.mode_normalization import rotation_columns
from ada.visit.rendering.fea_offscreen import _parse_afbv, beam_solid_displacement


def test_the_warp_map_reads_back_what_the_bake_wrote(tmp_path):
    mesh = SimpleNamespace(
        vertex_node0=np.array([0, 0, 1]), vertex_node1=np.array([1, 1, 2]), vertex_t=np.array([0.0, 0.5, 1.0])
    )
    path = tmp_path / "fea.beam_solids.warp.bin"
    write_beam_solids_warp(mesh, path)
    node0, node1, t = _parse_afbv(path.read_bytes())
    assert node0.tolist() == [0, 0, 1]
    assert node1.tolist() == [1, 1, 2]
    assert t.tolist() == [0.0, 0.5, 1.0]


def test_a_malformed_warp_map_is_refused():
    assert _parse_afbv(b"NOPE" + b"\x00" * 12) is None


def test_a_twist_turns_the_section_about_the_beam_axis():
    # Beam along X from (0,0,0) to (2,0,0); a solid vertex at mid-span, 0.1 off the axis in +Y.
    # Node 1 twisted 0.2 rad about X, so 0.1 rad at mid-span: the vertex swings 0.01 toward +Z.
    nodes = np.array([[0.0, 0, 0], [2.0, 0, 0]])
    values = np.array([[0, 0, 0, 0, 0, 0], [0, 0, 0, 0.2, 0, 0]], dtype=float)
    delta = beam_solid_displacement(
        np.array([[1.0, 0.1, 0]]),
        np.array([0]),
        np.array([1]),
        np.array([0.5]),
        values,
        ["U1", "U2", "U3", "UR1", "UR2", "UR3"],
        nodes,
    )
    np.testing.assert_allclose(delta, [[0, 0, 0.01]], atol=1e-12)


def test_without_named_rotations_it_is_translation_only():
    values = np.array([[0, 1, 0], [0, 3, 0]], dtype=float)
    delta = beam_solid_displacement(
        np.array([[1.0, 0.1, 0]]), np.array([0]), np.array([1]), np.array([0.5]), values, ["X", "Y", "Z"], None
    )
    np.testing.assert_allclose(delta, [[0, 2, 0]])


@pytest.mark.parametrize(
    "components, expected",
    [
        (["ALL", "X", "Y", "Z", "RX", "RY", "RZ"], [4, 5, 6]),
        (["DX", "DY", "DZ", "DRX", "DRY", "DRZ"], [3, 4, 5]),
        (["U1", "U2", "U3", "UR1", "UR2", "UR3"], [3, 4, 5]),
        (["X", "Y", "Z"], None),
    ],
)
def test_rotation_columns_by_name(components, expected):
    assert rotation_columns(components) == expected

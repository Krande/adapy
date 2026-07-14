import numpy as np

import ada
from ada.config import Config
from ada.geom.solids import Box


def test_read_shape_w_transformation(example_files):
    a = ada.from_ifc(example_files / "ifc_files/mapped_shapes/mapped-shape-with-transformation.ifc")
    _ = a.to_ifc(file_obj_only=True)
    print(a)


def test_placement_from_ifc_4x4_roundtrips():
    """The IFC placement helper must produce a Placement whose get_matrix4x4 — which the
    scene applies as M@p — reproduces the input world matrix. from_4x4_matrix alone returns
    the transpose (columns vs rows), which renders axis-swapping placements wrong."""
    from ada.cadit.ifc.read.geom.placement import placement_from_ifc_4x4

    # world transform: 90deg about Z + translation (a genuine axis swap, not symmetric)
    m = np.array([[0.0, -1.0, 0.0, -0.42], [1.0, 0.0, 0.0, -0.29], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]])
    p = placement_from_ifc_4x4(m)
    assert np.allclose(p.get_matrix4x4(), m)
    # local X (1,0,0) maps to the rotated world direction, not back to (1,0,0)
    assert np.allclose((p.get_matrix4x4() @ np.array([1.0, 0, 0, 1]))[:3], (-0.42, 0.71, 0.0))


def test_read_rotated_box(example_files):
    Config().update_config_globally("ifc_import_shape_geom", True)
    a = ada.from_ifc(example_files / "ifc_files/box_rotated.ifc")
    door1 = a.get_by_name("door1")
    door2 = a.get_by_name("door2")

    assert isinstance(door1.geom.geometry, Box)
    assert isinstance(door2.geom.geometry, Box)

    # door2 is door1 rotated 90deg about Z: its placement must carry that rotation (local X
    # -> world +Y), where the transpose bug previously left it wrong.
    r2 = np.array(door2.placement.get_matrix4x4())[:3, :3]
    assert np.allclose(r2 @ np.array([1.0, 0, 0]), (0.0, 1.0, 0.0), atol=1e-6)
    r1 = np.array(door1.placement.get_matrix4x4())[:3, :3]
    assert np.allclose(r1, np.eye(3), atol=1e-6)


def test_rotated_shape_placement_roundtrips(tmp_path):
    """A shape under a rotated part placement survives an IFC write->read with the placement
    applied correctly: box corner (3,1,2) local -> world under a 90deg-about-Z frame at (0,5,0)."""
    from ada import Placement

    box = ada.PrimBox("b", (0, 0, 0), (3, 1, 2))
    part = ada.Part("P", placement=Placement(origin=(0, 5, 0), xdir=(0, 1, 0), ydir=(-1, 0, 0), zdir=(0, 0, 1)))
    (ada.Assembly() / (part / box)).to_ifc(tmp_path / "rotshape.ifc")

    b = ada.from_ifc(tmp_path / "rotshape.ifc")
    o = next(iter(b.get_all_physical_objects()))
    m = o.placement.get_matrix4x4()
    assert np.allclose((m @ np.array([0.0, 0, 0, 1]))[:3], (0.0, 5.0, 0.0), atol=1e-6)
    assert np.allclose((m @ np.array([3.0, 1, 2, 1]))[:3], (-1.0, 8.0, 2.0), atol=1e-6)

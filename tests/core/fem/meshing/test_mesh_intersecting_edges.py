import numpy as np

import ada
from ada.core.utils import Counter
from ada.fem.shapes.definitions import LineShapes


def test_edges_intersect(test_meshing_dir):
    bm_name = Counter(1, "bm")
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)
    points = pl.poly.points3d
    objects = [pl]

    # Beams along 3 of 4 along circumference
    for p1, p2 in zip(points[:-1], points[1:]):
        objects.append(ada.Beam(next(bm_name), p1, p2, "IPE100"))

    # Beam along middle in x-dir
    bmx = ada.Beam(next(bm_name), (0, 0.5, 0.0), (1, 0.5, 0), "IPE100")
    objects.append(bmx)

    # Beam along diagonal
    bm_diag = ada.Beam(next(bm_name), (0, 0, 0.0), (1, 1, 0), "IPE100")
    objects.append(bm_diag)

    a = ada.Assembly() / (ada.Part("MyPart") / objects)
    p = a.get_part("MyPart")
    # p.connections.find()

    p.fem = p.to_fem_obj(0.1, interactive=False)

    n = p.fem.nodes.get_by_volume(p=(0.5, 0.5, 0))[0]
    assert len(list(filter(lambda x: x.type == LineShapes.LINE, n.refs))) == 4
    # a.to_fem("MyIntersectingedge_ufo", "usfos", overwrite=True, scratch_dir=test_meshing_dir)
    # a.to_ifc(test_meshing_dir / "IntersectingFEM", include_fem=False)


def test_crossing_free_beams():
    bm1 = ada.Beam("bm1", (0, 0.5, 0.0), (1, 0.5, 0), "IPE100")
    bm2 = ada.Beam("bm2", (0, 0, 0.0), (1, 1, 0), "IPE100")
    a = ada.Assembly() / (ada.Part("XBeams") / (bm1, bm2))
    a.fem = a.to_fem_obj(0.1, experimental_bm_splitting=True)

    n = a.fem.nodes.get_by_volume(p=(0.5, 0.5, 0))[0]
    assert len(list(filter(lambda x: x.type == LineShapes.LINE, n.refs))) == 4

    # a.to_fem("MyIntersectingedge_ufo", "usfos", overwrite=True)


def test_beams_enclosing_beams():
    p1x1 = np.array([(0, 0), (1, 0), (1, 1), (0, 1)])
    pl = ada.Plate("pl1", p1x1 * 5, 10e-3)
    # add a new row to p1x1 from p1x1[0]
    p1x1 = np.vstack((p1x1, p1x1[0]))
    f_ = np.zeros((5, 1))
    inner_points = ada.Point(2.1, 2.1, 0) + np.hstack((p1x1, f_))
    beams_inner = ada.Beam.array_from_list_of_coords(inner_points, "IPE100")

    # Multiply p1x1 with 5 and append a new column of value 0
    p5x5 = np.hstack((p1x1 * 5, f_))
    beams = ada.Beam.array_from_list_of_coords(p5x5, "IPE100")

    a = ada.Assembly() / (ada.Part("XBeams") / (pl, *beams, *beams_inner))
    a.fem = a.to_fem_obj(0.3, interactive=False)
    for el in a.fem.elements.lines:
        assert el.fem_sec is not None
    # a.to_fem("MyIntersectingedge_aba", "abaqus", overwrite=True, scratch_dir='temp/abaqus')
    # a.to_fem("MyIntersectingedge_ufo", "usfos", overwrite=True, scratch_dir='temp/usfos')

import ada
from ada.core.utils import Counter

test_dir = ada.config.Settings.test_dir / "meshing"


def test_edges_intersect():
    bm_name = Counter(1, "bm")
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)
    points = pl.poly.points3d
    objects = [pl]

    # Beams along 3 of 4 along circumference
    for p1, p2 in zip(points[:-1], points[1:]):
        objects.append(ada.Beam(next(bm_name), p1, p2, "IPE100"))

    # Beam along middle in x-dir
    objects.append(ada.Beam(next(bm_name), (0, 0.5, 0.0), (1, 0.5, 0), "IPE100"))

    # Beam along diagonal
    objects.append(ada.Beam(next(bm_name), (0, 0, 0.0), (1, 1, 0), "IPE100"))

    a = ada.Assembly() / (ada.Part("MyPart") / objects)
    p = a.get_part("MyPart")
    # p.connections.find()

    p.fem = p.to_fem_obj(0.1, interactive=False)

    # a.to_fem("MyIntersectingedge_ufo", "usfos", overwrite=True, scratch_dir=test_dir)
    # a.to_ifc(test_dir / "IntersectingFEM", include_fem=False)

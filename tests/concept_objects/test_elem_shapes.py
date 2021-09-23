import unittest

from ada import Assembly, Part, PrimBox, PrimCyl, PrimExtrude, PrimRevolve, PrimSweep
from ada.config import Settings
from ada.param_models.basic_module import SimpleStru

test_dir = Settings.test_dir / "shapes"


class TestShapesExport(unittest.TestCase):
    def test_export_primbox(self):
        p1 = (0.2, 0.2, 2)
        p2 = (1.2, 1.2, 4)

        a = Assembly("Site") / [
            SimpleStru("SimpleStru"),
            PrimBox("VolBox", p1, p2),
            PrimCyl("VolCyl", (2, 2, 2), (4, 4, 4), 0.2),
            PrimExtrude("VolExtrude", [(0, 0), (1, 0), (0.5, 1)], 2, (0, 0, 1), (2, 2, 2), (1, 0, 0)),
        ]
        a.to_ifc(test_dir / "world_of_shapes.ifc")

    def test_export_primrevolve(self):
        a = Assembly("Site") / SimpleStru("SimpleStru")
        points = [(0, 0), (1, 0), (0.5, 1)]
        origin = (2, 2, 3)
        xdir = (0, 0, 1)
        normal = (1, 0, 0)
        rev_angle = 275
        a.add_shape(PrimRevolve("VolRevolve", points, origin, xdir, normal, rev_angle))
        a.to_ifc(test_dir / "world_shape_revolve.ifc")

    def test_sweep_shape(self):
        sweep_curve = [(0, 0, 0), (5, 5.0, 0.0, 1), (10, 0, 0)]
        ot = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
        shape = PrimSweep("MyShape", sweep_curve, (0, 1, 0), (1, 0, 0), ot)

        a = Assembly("SweptShapes", units="m") / [Part("MyPart") / [shape]]
        a.to_ifc(test_dir / "my_swept_shape_m.ifc")

        # my_renderer = x3dom_renderer.X3DomRenderer()
        # my_renderer.DisplayShape(shape.profile_curve_outer.wire)
        # my_renderer.DisplayShape(shape.sweep_curve.wire)
        # my_renderer.DisplayShape(shape.geom)
        # my_renderer.render()

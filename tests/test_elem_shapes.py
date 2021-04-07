import unittest

from ada import Assembly, Part, PrimBox, PrimCyl, PrimExtrude, PrimRevolve, PrimSweep
from ada.config import Settings
from ada.param_models.basic_module import SimpleStru

test_folder = Settings.test_dir / "shapes"

p1 = (2, 2, 2)
p2 = (4, 4, 4)


class TestShapesExport(unittest.TestCase):
    def test_export_primbox(self):
        a = Assembly("Site") / SimpleStru("SimpleStru")

        a.add_shape(PrimBox("VolBox", p1, p2))
        a.to_ifc(test_folder / "world_shape_box.ifc")

    def test_export_primcyl(self):
        a = Assembly("Site") / SimpleStru("SimpleStru")
        a.add_shape(PrimCyl("VolCyl", (2, 2, 2), (4, 4, 4), 0.2))
        a.to_ifc(test_folder / "world_shape_cyl.ifc")

    def test_export_primrextrude(self):
        a = Assembly("Site") / SimpleStru("SimpleStru")
        a.add_shape(PrimExtrude("VolExtrude", [(0, 0), (1, 0), (0.5, 1)], 2, (0, 0, 1), (2, 2, 2), (1, 0, 0)))
        a.to_ifc(test_folder / "world_shape_extrude.ifc")


class TestAdvanced(unittest.TestCase):
    def test_export_primrevolve(self):
        a = Assembly("Site") / SimpleStru("SimpleStru")
        points = [(0, 0), (1, 0), (0.5, 1)]
        origin = (2, 2, 3)
        xdir = (0, 0, 1)
        normal = (1, 0, 0)
        rev_angle = 275
        a.add_shape(PrimRevolve("VolRevolve", points, origin, xdir, normal, rev_angle))
        a.to_ifc(test_folder / "world_shape_revolve.ifc")

    def test_sweep_shape(self):
        sweep_curve = [(0, 0, 0), (5, 5.0, 0.0, 1), (10, 0, 0)]
        ot = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
        shape = PrimSweep("MyShape", sweep_curve, (0, 1, 0), (1, 0, 0), ot)

        a = Assembly("SweptShapes", units="m") / [Part("MyPart") / [shape, PrimBox("VolBox", p1, p2)]]
        a.to_ifc(test_folder / "my_swept_shape_m.ifc")

        # my_renderer = x3dom_renderer.X3DomRenderer()
        # my_renderer.DisplayShape(shape.profile_curve_outer.wire)
        # my_renderer.DisplayShape(shape.sweep_curve.wire)
        # my_renderer.DisplayShape(shape.geom)
        # my_renderer.render()

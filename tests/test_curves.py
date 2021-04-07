import unittest

from OCC.Display.WebGl import x3dom_renderer

from ada import CurvePoly

# Settings.debug = True
sweep_curve = [(1, 1, 0), (5, 5.0, 0.0, 1), (10, 1, 0)]
ot = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]


class SweepTests(unittest.TestCase):
    def test_sweep_curve(self):
        curve = CurvePoly(points3d=sweep_curve, is_closed=False)
        my_renderer = x3dom_renderer.X3DomRenderer()
        my_renderer.DisplayShape(curve.wire, export_edges=True)

        # my_renderer.render()


if __name__ == "__main__":
    unittest.main()

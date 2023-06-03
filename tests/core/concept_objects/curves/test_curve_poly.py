from OCC.Display.WebGl import x3dom_renderer

from ada.concepts.curves import CurveSweep


def test_sweep_curve():
    sweep_curve = [(1, 1, 0), (5, 5.0, 0.0, 1), (10, 1, 0)]
    curve = CurveSweep.from_3d_points(sweep_curve)
    my_renderer = x3dom_renderer.X3DomRenderer()
    my_renderer.DisplayShape(curve.wire(), export_edges=True)
    # my_renderer.render()

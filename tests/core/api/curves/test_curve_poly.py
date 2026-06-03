import pytest

from ada.api.curves import CurveOpen2d
from ada.cad import active_backend


@pytest.mark.skipif(
    active_backend().name == "adacpp",
    reason="OCC x3dom display + CurveOpen2d.occ_wire is pythonocc-only (display smoke test)",
)
def test_sweep_curve():
    from OCC.Display.WebGl import x3dom_renderer

    sweep_curve = [(1, 1, 0), (5, 5.0, 0.0, 1), (10, 1, 0)]
    curve = CurveOpen2d.from_3d_points(sweep_curve)
    my_renderer = x3dom_renderer.X3DomRenderer()
    my_renderer.DisplayShape(curve.occ_wire(), export_edges=True)
    # my_renderer.render()

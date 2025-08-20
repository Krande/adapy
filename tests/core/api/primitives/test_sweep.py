import ada

def test_sweep_shape(tmp_path):
    sweep_curve = [(0, 0, 0), (5, 5.0, 0.0, 1), (10, 0, 0)]
    ot = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
    shape = ada.PrimSweep("MyShape", sweep_curve, ot)

    a = ada.Assembly("SweptShapes", units="m") / [ada.Part("MyPart") / [shape]]
    a.to_ifc(tmp_path / "swept_shape.ifc", validate=True)
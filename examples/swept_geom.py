import ada


def straight_sweep():
    sweep_curve = [(0, 0.1, 0.2), (1, 0.1, 0.2)]
    profile = [(0, 0), (1e-2, 0), (0.5e-2, 1e-2)]
    sweep = ada.PrimSweep('sweep', sweep_curve, profile, (1, 0, 0), (0, 1, 0))
    sweep.show(stream_from_ifc_store=True)
    (ada.Assembly(schema="IFC4") / sweep).to_ifc("temp/sweep.ifc", validate=True)

def curved_sweep():
    curve3d = [
        (0, 0, 0),
        (0.5, 0.5, 0.5, 0.2),
        (0.5, 1, 0.5),
        (1, 1, 0.5),
    ]
    profile2d = [(0, 0), (1, 0), (1, 1), (0, 1)]
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, color="red")

    a = ada.Assembly("SweptShapes") / [ada.Part("MyPart") / [sweep]]
    a.to_ifc("temp/my_swept_shape_m.ifc", file_obj_only=False, validate=True)

if __name__ == '__main__':
    # curved_sweep()
    straight_sweep()

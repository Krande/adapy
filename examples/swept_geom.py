import ada
from ada import CurvePoly2d

profile2d = [(0, 0), (1e-2, 0), (0.5e-2, 1e-2)]

def straight_sweep_x():
    sweep_curve = [(0, 0.1, 0.2), (1, 0.1, 0.2)]
    profile = profile2d
    sweep = ada.PrimSweep('sweep_x', sweep_curve, profile)
    sweep.show(stream_from_ifc_store=True)
    (ada.Assembly() / sweep).to_ifc("temp/sweep_x.ifc", validate=True)

def straight_sweep_y():
    sweep_curve = [(0, 0.1, 0.2), (0, 0.1, 1.0)]
    profile = profile2d
    # profile = CurvePoly2d(points2d=profile2d, origin=(0,0,0), normal=(0,1,0), xdir=(1,0,0))
    sweep = ada.PrimSweep('sweep_y', sweep_curve, profile, placement=ada.Placement(zdir=(0,1,0), xdir=(1,0,0)))
    sweep.show(stream_from_ifc_store=True)
    (ada.Assembly() / sweep).to_ifc("temp/sweep_y.ifc", validate=True)

def straight_sweep_z():
    sweep_curve = [(0, 0.1, 0.2), (0, 0.1, 0.8)]
    profile = profile2d
    sweep = ada.PrimSweep('sweep_z', sweep_curve, profile)
    sweep.show(stream_from_ifc_store=True)
    (ada.Assembly() / sweep).to_ifc("temp/sweep_z.ifc", validate=True)

def curved_sweep():
    curve3d = [
        (0, 0, 0),
        (0.5, 0.5, 0.5, 0.2),
        (0.5, 1, 0.5),
        (1, 1, 0.5),
    ]
    profile2d = [(0, 0), (1e-2, 0), (1e-2, 1e-2), (0, 1e-2)]
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, color="red", derived_reference=True)
    sweep.show(stream_from_ifc_store=True)
    a = ada.Assembly("SweptShapes") / [ada.Part("MyPart") / [sweep]]
    a.to_ifc("temp/my_swept_shape_m.ifc", file_obj_only=False, validate=True)

if __name__ == '__main__':
    # curved_sweep()
    # straight_sweep_x()
    straight_sweep_y()
    # straight_sweep_z()

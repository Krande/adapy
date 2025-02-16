import ada
from ada.base.ifc_types import SpatialTypes

ada.config.logger.setLevel("DEBUG")
profile2d = [(0, 0), (1e-2, 0), (0.5e-2, 1e-2)]


def straight_sweep_x(append=False):
    sweep_curve = [(0, 0.1, 0.2), (1, 0.1, 0.2)]
    profile = profile2d
    sweep = ada.PrimSweep("sweep_x", sweep_curve, profile)
    sweep.show(stream_from_ifc_store=True, append_to_scene=append)
    (ada.Assembly() / (ada.Part('MySweep', ifc_class=SpatialTypes.IfcBuilding) / sweep)).to_ifc("temp/sweep_x.ifc", validate=True)


def straight_sweep_y(append=False):
    sweep_curve = [(0, 0.1, 0.2), (0, 1.0, 0.2)]
    profile = profile2d
    sweep = ada.PrimSweep("sweep_y", sweep_curve, profile)
    sweep.show(stream_from_ifc_store=True, append_to_scene=append)
    (ada.Assembly() / (ada.Part('MySweep') / sweep)).to_ifc("temp/sweep_y.ifc", validate=True)


def straight_sweep_z(append=False):
    sweep_curve = [(0, 0.1, 0.2), (0, 0.1, 0.8)]
    profile = profile2d
    sweep = ada.PrimSweep("sweep_z", sweep_curve, profile)
    sweep.show(stream_from_ifc_store=True, append_to_scene=append)
    (ada.Assembly() / sweep).to_ifc("temp/sweep_z.ifc", validate=True)


def curved_sweep(append=False):
    curve3d = [
        (0, 0, 0),
        (0.5, 0.5, 0.5, 0.2),
        (0.5, 1, 0.5),
        (1, 1, 0.5),
    ]
    profile2d = [(0, 0), (1e-2, 0), (1e-2, 1e-2), (0, 1e-2)]
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, color="red", derived_reference=True)
    sweep.show(stream_from_ifc_store=True, append_to_scene=append)
    a = ada.Assembly("SweptShapes") / [ada.Part("MyPart") / [sweep]]
    a.to_ifc("temp/my_swept_shape_m.ifc", file_obj_only=False, validate=True)


if __name__ == "__main__":
    straight_sweep_x()
    straight_sweep_y(True)
    straight_sweep_z(True)
    curved_sweep(True)

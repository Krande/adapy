import ada
from ada.config import Config

#Config().ifc_use_index_poly_curve_segments = False

sweep = ada.PrimSweep(
    "sweep2",
    [(0, 0, 0), (0.0, 0, 0.5, 0.2), (0.01, 0.8, 1, 0.2), (0.8, 0.8, 2, 0.2), (0.8, 1.7, 2)],
    [(0, 0), (0.1, 0), (0.1, 0.05)],
    profile_normal=(0, 0, -1),
    profile_ydir=(0, 1, 0),
    origin=(50, 100, 200),
    # derived_reference=True
)


a = ada.Assembly() / sweep
a.to_ifc("temp/simple_sweep_2.ifc", validate=True)
a.show(stream_from_ifc_store=False, append_to_scene=False)
a.show(stream_from_ifc_store=True, append_to_scene=True)


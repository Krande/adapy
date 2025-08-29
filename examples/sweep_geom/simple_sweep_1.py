import ada
from ada.config import Config

# Config().ifc_use_index_poly_curve_segments = False

sweep = ada.PrimSweep(
    "sweep1",
    [(0, 0, 0), (0.5, 0, 0, 0.2), (0.8, 0.0, 1)],
    [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)],
    profile_normal=(1, 0, 0),
    profile_ydir=(0, 1, 0),
    derived_reference=False,
)

a = ada.Assembly() / sweep
a.to_ifc("temp/simple_sweep_1.ifc", validate=True)
a.to_stp("temp/simple_sweep_1.stp")
a.show(stream_from_ifc_store=False, append_to_scene=False)
a.show(stream_from_ifc_store=True, append_to_scene=True)

import ada

sweep = ada.PrimSweep(
    "sweep1",
    [(0, 0, 0), (0.5, 0, 0, 0.2), (0.8, 0.0, 1)],
    [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)],
    profile_normal=(1, 0, 0),
    origin=(0, 0, 0),
    profile_xdir=(0, 0, 1),
)

a = ada.Assembly() / sweep
a.show(stream_from_ifc_store=True)
a.to_ifc("temp/simple_sweep_1.ifc", validate=True)
a.to_stp("temp/simple_sweep_1.stp")
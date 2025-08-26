import ada

sweep = ada.PrimSweep(
    "sweep1",
    [(0, 0, 0), (0.0, 0, 0.5, 0.2), (0.01, 0.8, 1), (0.8, 0.8, 2), (0.8, 1.7, 2)],
    [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)],
    profile_normal=(0, 0, -1),
    origin=(0, 0, 0),
    profile_xdir=(0, 1, 0),
    # derived_reference=True
)


a = ada.Assembly() / sweep
a.to_ifc("temp/simple_sweep_2.ifc", validate=True)
a.show(stream_from_ifc_store=False, append_to_scene=False)
a.show(stream_from_ifc_store=True, append_to_scene=True)


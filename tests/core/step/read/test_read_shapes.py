import ada


def test_read_ventilator(example_files):
    a = ada.from_step(example_files / "step_files/Ventilator.stp")
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
    # geom = objects[0].geom
    # shape = int(geom.this)
    # import gmsh
    # gmsh.initialize()
    # ents = gmsh.model.occ.importShapesNativePointer(shape, highestDimOnly=True)
    # gmsh.model.occ.synchronize()
    # gmsh.fltk.run()
    # print(ents)

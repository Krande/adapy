import ada
from ada.fem.meshing.concepts import GmshOptions


def main(name="box", mesh_size=1.0, use_hex=True, elem_order=False, reduced_int=True):
    options = GmshOptions(Mesh_ElementOrder=elem_order)
    box = ada.PrimBox(name, (0, 0, 0), (1, 1, 1))
    p = ada.Part("boxPart") / box
    p.fem = p.to_fem_obj(mesh_size, use_hex=use_hex, options=options)

    a = ada.Assembly() / p
    # Create Step
    step = a.fem.add_step(
        ada.fem.StepImplicitStatic(
            "static",
            init_incr=1,
            max_incr=25,
            total_incr=100,
        )
    )
    fe_surf_top = box.bbox().sides.top(return_surface=True, surf_name="top")
    fe_nodes_btn = box.bbox().sides.bottom(return_fem_nodes=True)
    fe_btn_set = a.fem.add_set(ada.fem.FemSet("bottom", fe_nodes_btn))

    step.add_load(ada.fem.LoadPressure("pressure", 1e5, fe_surf_top))
    step.add_bc(ada.fem.Bc("fix", fe_btn_set, (1, 2, 3, 4, 5, 6)))
    step.add_history_output(ada.fem.HistOutput("displ", fe_btn_set, "node", ["U3"]))
    field = step.field_outputs[0]
    field.int_type = field.TYPES_INTERVAL.INTERVAL
    field.int_value = 2

    if reduced_int is False:
        p.fem.options.ABAQUS.default_elements.SOLID.HEXAHEDRON = "C3D8"
        p.fem.options.ABAQUS.default_elements.SOLID.HEXAHEDRON20 = "C3D20"
    else:
        p.fem.options.ABAQUS.default_elements.SOLID.HEXAHEDRON = "C3D8R"
        p.fem.options.ABAQUS.default_elements.SOLID.HEXAHEDRON20 = "C3D20R"

    a.to_fem(name, "abaqus", scratch_dir="temp/scratch", overwrite=True, execute=True)


if __name__ == "__main__":
    # main("hex1Rbox1x1x1", 2, elem_order=1)
    # main("hex2Rbox1x1x1", 2, elem_order=2)
    # main("hex1box1x1x1", 2, elem_order=1, reduced_int=False)
    # main("hex2box1x1x1", 2, elem_order=2, reduced_int=False)
    # main("tet2Rbox1x1x1", 2, elem_order=2, use_hex=False)
    main("tet2box1x1x1", 2, elem_order=2, use_hex=False, reduced_int=False)

    # main("hex1Rbox2x2x2", 0.5, elem_order=1)
    # main("hex2Rbox2x2x2", 0.5, elem_order=2)
    # main("tet2Rbox2x2x2", 0.5, elem_order=2, use_hex=False)

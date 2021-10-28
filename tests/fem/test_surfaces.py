import ada


def test_surface_box():
    # Build Model
    box = ada.PrimBox("MyBoxShape", (0, 0, 0), (1, 1, 1))
    a = ada.Assembly() / (ada.Part("MyBoxPart") / [box])

    # Create FEM mesh
    p = a.get_part("MyBoxPart")
    p.fem = p.to_fem_obj(0.1, "shell", interactive=False)

    # Add Step
    step = a.fem.add_step(ada.fem.StepImplicit("MyStep"))

    # Add Boundary condition
    btn_nodes = box.bbox.sides.bottom(return_fem_nodes=True)
    p.fem.add_bc(ada.fem.Bc("fix", ada.fem.FemSet("BottomNodes", btn_nodes), [1, 2, 3]))

    # Add surface load
    surface = p.fem.add_surface(box.bbox.sides.front(return_surface=True, surface_name="FrontSurface"))
    step.add_load(ada.fem.LoadPressure("PressureFront", 200, surface))

    a.to_fem("MyFemBox", "abaqus", overwrite=True)
    # a.to_fem("MyFemBox_ca", "code_aster", overwrite=True)


def test_surface_beam():
    from ada.fem.meshing import GmshOptions

    # Build Model
    bm = ada.Beam("MyBeam", (0, 0, 0), (0, 0, 1), "BG200x150x6x6")
    p = ada.Part("MyBmPart") / [bm]
    a = ada.Assembly() / p

    # Create FEM mesh
    p.fem = p.to_fem_obj(0.10, "solid", interactive=False, options=GmshOptions(Mesh_ElementOrder=2))

    # Add Step
    step = a.fem.add_step(ada.fem.StepImplicit("MyStep"))

    # Add Boundary Condition
    start_of_beam = bm.bbox.sides.back(return_fem_nodes=True)
    p.fem.add_bc(ada.fem.Bc("fix", ada.fem.FemSet("bc_fix", start_of_beam), [1, 2, 3]))

    # Add Surface Load
    surface_top = p.fem.add_surface(bm.bbox.sides.top(return_surface=True, surf_name="TopSurface"))
    step.add_load(ada.fem.LoadPressure("PressureTop", 1e6, surface_top))

    a.to_fem("MyFemBeam_100mm_2nd_order", "abaqus", overwrite=True, execute=False)

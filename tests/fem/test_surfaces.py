import ada


def test_surface_box():
    # Build Model
    a = ada.Assembly() / (ada.Part("MyBoxPart") / ada.PrimBox("MyBoxShape", (0, 0, 0), (1, 1, 1)))

    # Create FEM mesh
    p = a.get_part("MyBoxPart")
    p.fem = p.to_fem_obj(1.0, "solid", interactive=False)

    # Add Step
    _ = a.fem.add_step(ada.fem.StepImplicit("MyStep"))

    # Add surfaces
    box = a.get_by_name("MyBoxShape")
    print(box)
    el = p.fem.elements[0]
    print(el)
    a.to_fem("MyFemBox", "abaqus", overwrite=True)

    # TODO: Specify surfaces on elements on the East and North side of this box and assign pressure and surface traction
    #   (or shear if you will)

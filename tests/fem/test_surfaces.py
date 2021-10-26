import ada


def test_surface_box():
    # Build Model
    a = ada.Assembly() / (ada.Part("MyBoxPart") / ada.PrimBox("MyBoxShape", (0, 0, 0), (1, 1, 1)))

    # Create FEM mesh
    p = a.get_part("MyBoxPart")
    p.fem = p.to_fem_obj(1.0, "solid", interactive=False)

    # Add Step
    step = a.fem.add_step(ada.fem.StepImplicit("MyStep"))

    # Add surfaces

    box: ada.PrimBox = a.get_by_name("MyBoxShape")

    front_nodes = box.sides.front(return_fem_nodes=True)
    btn_nodes = box.sides.bottom(return_fem_nodes=True)
    p.fem.add_set(ada.fem.FemSet("FrontNodes", front_nodes))
    fs_btn = p.fem.add_set(ada.fem.FemSet("BottomNodes", btn_nodes))
    p.fem.add_bc(ada.fem.Bc("fix", fs_btn, [1, 2, 3]))
    elements = []
    face_seq_indices = {}
    for n in front_nodes:
        for el in n.refs:
            has_parallel_face = False
            for i, nid_refs in enumerate(el.shape.faces_seq):
                all_face_nodes_in_plane = True
                for nid in nid_refs:
                    no = el.nodes[nid]
                    if no not in front_nodes:
                        all_face_nodes_in_plane = False
                if all_face_nodes_in_plane is True:
                    has_parallel_face = True
                    face_seq_indices[el] = i

            if has_parallel_face is True:
                elements.append(el)
    # el_sets: List[ada.fem.FemSet] = []
    # for el, face_seq_ref in face_seq_indices.items():
    #     side_name = f"S{face_seq_ref}"
    #
    #     el_sets.append(fs_elem)

    fs_elem = p.fem.add_set(ada.fem.FemSet("FrontElements", elements))
    surface = p.fem.add_surface(
        ada.fem.Surface("FrontSurfaceElem", ada.fem.Surface.TYPES.ELEMENT, fs_elem, face_id_label="S2")
    )
    step.add_load(ada.fem.LoadPressure("MyPressureLoad", 200, surface))
    print(box)
    el = p.fem.elements[0]
    print(el)
    a.to_fem("MyFemBox", "abaqus", overwrite=True)
    a.to_fem("MyFemBox_ca", "code_aster", overwrite=True)

    # TODO: Specify surfaces on elements on the East and North side of this box and assign pressure and surface traction
    #   (or shear if you will)

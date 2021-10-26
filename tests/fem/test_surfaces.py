from typing import List

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

    box: ada.PrimBox = a.get_by_name("MyBoxShape")

    front = box.sides.front()
    face_nodes = p.fem.nodes.get_by_volume(p=front[0], vol_box=front[1])
    p.fem.add_set(ada.fem.FemSet("FrontNodes", face_nodes))

    elements = []
    face_seq_indices = {}
    for n in face_nodes:
        for el in n.refs:
            has_parallel_face = False
            for i, nid_refs in enumerate(el.shape.faces_seq):
                all_face_nodes_in_plane = True
                for nid in nid_refs:
                    no = el.nodes[nid]
                    if no not in face_nodes:
                        all_face_nodes_in_plane = False
                if all_face_nodes_in_plane is True:
                    has_parallel_face = True
                    face_seq_indices[el] = i

            if has_parallel_face is True:
                elements.append(el)
    el_sets: List[ada.fem.FemSet] = []
    # for el, face_seq_ref in face_seq_indices.items():
    #     side_name = f"S{face_seq_ref}"
    #
    #     el_sets.append(fs_elem)
    fs_elem = p.fem.add_set(ada.fem.FemSet(f"FrontElements", elements))
    p.fem.add_surface(
        ada.fem.Surface(f"FrontSurfaceElem", ada.fem.Surface.TYPES.ELEMENT, fs_elem, face_id_label="S2")
    )
    print(box)
    el = p.fem.elements[0]
    print(el)
    a.to_fem("MyFemBox", "abaqus", overwrite=True)

    # TODO: Specify surfaces on elements on the East and North side of this box and assign pressure and surface traction
    #   (or shear if you will)

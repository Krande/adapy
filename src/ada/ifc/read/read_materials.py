from ada import Assembly, Material


def read_ifc_materials(f, a: Assembly):
    for ifc_mat in f.by_type("IfcMaterial"):
        mat = a.add_material(Material(ifc_mat.Name, ifc_mat=ifc_mat))

        print(mat)

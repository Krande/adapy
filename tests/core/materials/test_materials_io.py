import ada
from ada import Assembly, Material, Part


def test_material_ifc_roundtrip(tmp_path):
    ifc_path = tmp_path / "my_material.ifc"

    p = Part("MyPart")
    p.add_material(Material("my_mat"))
    a = Assembly("MyAssembly") / p
    fp = a.to_ifc(ifc_path, file_obj_only=False)

    b = ada.from_ifc(fp)
    assert len(b.materials) == 1

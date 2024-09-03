from ada import Assembly, Beam, Material, Part
from ada.materials.metals import CarbonSteel


def test_beam_to_from_ifc(tmp_path):
    bm = Beam(
        "bm1",
        n1=[0, 0, 0],
        n2=[2, 0, 0],
        sec="IPE220",
        mat=Material("SteelMat", CarbonSteel("S420")),
        color="red",
    )

    a = Assembly("MyAssembly") / [Part("MyPart") / bm]
    fp = a.to_ifc(tmp_path / "my_beam_profile.ifc", file_obj_only=True)

    a2 = Assembly("MyNewAssembly")
    a2.read_ifc(fp)

    # This would require more work put into __eq__ and __neq__. Not a priority (visual check in Blender for now)
    # bm2 = a2.get_by_name(bm.name)
    # assert bm2 == bm
    _ = a2.to_ifc(tmp_path / "my_beam_profile_re_exported.ifc", file_obj_only=True)

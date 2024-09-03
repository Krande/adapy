from ada import Assembly, Beam, Part


def test_beam_sections_consolidate():
    bm1 = Beam("bm1", (0, 0, 0), (1, 0, 0), "HP140x8")
    bm2 = Beam("bm2", (0, 0, 0), (1, 0, 0), "HP140x8")
    bm3 = Beam("bm3", (0, 0, 0), (1, 0, 0), "HP140x8")

    a = Assembly() / [(Part("P1") / bm1), (Part("P2") / bm2), (Part("P3") / bm3)]
    assert len(a.get_all_materials()) == 3
    a.consolidate_materials()
    assert len(a.get_all_materials()) == 1

    a.to_ifc("temp/bm_sec_validate.ifc", file_obj_only=False, validate=True)


def test_mixed_materials_consolidate(mixed_model):
    assert len(mixed_model.get_all_materials()) == 4
    mixed_model.consolidate_materials()
    assert len(mixed_model.get_all_materials()) == 2

    all_mat_map = {mat.guid: mat for mat in mixed_model.get_all_materials()}
    for obj in mixed_model.get_all_physical_objects():
        assert obj.material.guid in all_mat_map.keys()

    mixed_model.to_ifc(file_obj_only=True, validate=True)

from ada import Assembly, Beam, Part


def test_beam_sections_consolidate(tmp_path):
    bm1 = Beam("bm1", (0, 0, 0), (1, 0, 0), "HP140x8")
    bm2 = Beam("bm2", (0, 0, 0), (1, 0, 0), "HP140x8")
    bm3 = Beam("bm3", (0, 0, 0), (1, 0, 0), "HP140x8")

    a = Assembly() / [(Part("P1") / bm1), (Part("P2") / bm2), (Part("P3") / bm3)]
    assert len(a.get_all_materials()) == 3
    a.consolidate_materials()
    assert len(a.get_all_materials()) == 1

    a.to_ifc(tmp_path / "bm_sec_validate.ifc", file_obj_only=False, validate=True)


def test_mixed_materials_consolidate(mixed_model, tmp_path):
    assert len(mixed_model.get_all_materials()) == 4
    mixed_model.consolidate_materials()
    assert len(mixed_model.get_all_materials()) == 2

    all_mat_map = {mat.guid: mat for mat in mixed_model.get_all_materials()}
    for obj in mixed_model.get_all_physical_objects():
        assert obj.material.guid in all_mat_map.keys()

    mixed_model.to_ifc(tmp_path / "mixed_mat_validate.ifc", file_obj_only=False, validate=True)


def test_curved_plates_of_two_genie_xmls_consolidate(example_files, tmp_path):
    """Two GeniE XMLs each with a material "S355" and curved shells (PlateCurved, not a Plate subclass): the
    consolidation stopped with ``NotImplementedError: Not yet support section ... PlateCurved``."""
    import ada

    xml = example_files / "fem_files" / "sesam" / "curved_plates.xml"
    a = Assembly("A")
    for i in range(2):
        for p in ada.from_genie_xml(xml).get_all_parts_in_assembly():
            p.name = f"{p.name}_{i}"
            a.add_part(p)
    curved = list(a.get_all_physical_objects(by_type=ada.PlateCurved))
    assert curved
    assert len(a.get_all_materials()) == 2

    a.consolidate_materials()

    (mat,) = a.get_all_materials()
    assert all(pl.material is mat for pl in curved)
    a.to_genie_xml(tmp_path / "two_xmls.xml")

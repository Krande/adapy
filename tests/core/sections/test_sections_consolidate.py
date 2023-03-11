import pytest

from ada import Assembly, Beam, Part, Plate, from_ifc


@pytest.fixture
def three_beams():
    bm1 = Beam("bm1", (0, 0, 0), (1, 0, 0), "HP140x8")
    bm2 = Beam("bm2", (0, 0, 0), (1, 0, 0), "HP140x8")
    bm3 = Beam("bm3", (0, 0, 0), (1, 0, 0), "HP140x8")

    return Assembly() / [(Part("P1") / bm1), (Part("P2") / bm2), (Part("P3") / bm3)]


def test_beam_sections_consolidate(three_beams):
    a = three_beams
    assert len(a.get_all_sections()) == 3
    a.consolidate_sections()
    assert len(a.get_all_sections()) == 1

    a.to_ifc(file_obj_only=True, validate=True)


def test_mixed_sections_consolidate(mixed_model):
    assert len(mixed_model.get_all_sections()) == 4
    all_sec_map = {sec.guid: sec for sec in mixed_model.get_all_sections()}
    for obj in mixed_model.get_all_physical_objects():
        if isinstance(obj, Plate):
            continue
        assert obj.section.guid in all_sec_map.keys()

    mixed_model.consolidate_sections()
    assert len(mixed_model.get_all_sections()) == 2

    all_sec_map = {sec.guid: sec for sec in mixed_model.get_all_sections()}
    for obj in mixed_model.get_all_physical_objects():
        if isinstance(obj, Plate):
            continue
        if obj.section.guid not in all_sec_map.keys():
            raise ValueError()
        if hasattr(obj, "taper") and obj.taper is not None and obj.taper.guid not in all_sec_map.keys():
            raise ValueError()

    mixed_model.to_ifc(file_obj_only=True, validate=True)


def test_mixed_multi_sync_sections_consolidate(three_beams, pipe_w_multiple_bends):
    assert len(three_beams.get_all_sections()) == 3
    three_beams.ifc_store.sync()
    three_beams.add_object(pipe_w_multiple_bends)
    three_beams.to_ifc(file_obj_only=True, validate=True)


def test_mixed_roundtrip_sections_consolidate(three_beams, pipe_w_multiple_bends):
    assert len(three_beams.get_all_sections()) == 3
    f_res = three_beams.to_ifc(file_obj_only=True)

    a = from_ifc(f_res)
    p2 = a.get_by_name("P2")
    p2.add_object(pipe_w_multiple_bends)

    a.to_ifc(file_obj_only=True, validate=True)

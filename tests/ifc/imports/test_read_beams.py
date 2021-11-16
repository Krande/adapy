import ada


def test_read_standard_case_beams(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beam-standard-case.ifc")
    p = a.get_part("Building")
    assert len(p.beams) == 18

    # a.to_ifc(ifc_test_dir / "beam_standard_case_re-export.ifc")
    # p.fem = p.to_fem_obj(0.1, "line")
    # a.to_fem("read_standard_case_beams", "usfos", overwrite=True)

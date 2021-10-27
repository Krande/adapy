import ada
from ada.fem.formats.utils import default_fem_inp_path


def test_merge_fem_sections():
    bm1 = ada.Beam("Bm1", (0, 0, 0), (10, 0, 0), "IPE300")
    bm2 = ada.Beam("Bm2", (10, 0, 0), (10, 10, 0), "IPE300")
    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])

    p = a.get_part("MyPart")
    p.fem = p.to_fem_obj(2, "line")
    assert len(p.fem.sections) == 2

    name = "MergesSections"
    fem_format = "sesam"

    a.to_fem(name, fem_format, overwrite=True)

    inp_path = default_fem_inp_path(name, ada.config.Settings.scratch_dir)[fem_format]
    b = ada.from_fem(inp_path)
    pb = b.get_part("T1")
    assert len(pb.fem.sections) == 10

    pb.fem.sections.merge_by_properties()

    assert len(pb.fem.sections) == 2

    # b.to_fem("SectionsMerged", "abaqus", overwrite=True)

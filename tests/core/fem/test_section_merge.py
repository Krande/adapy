import ada
from ada.fem.formats.general import FEATypes
from ada.fem.formats.utils import default_fem_inp_path
from ada.sections.properties import GeneralProperties


def test_merge_fem_sections(tmp_path):
    bm1 = ada.Beam("Bm1", (0, 0, 0), (10, 0, 0), "IPE300")
    bm2 = ada.Beam("Bm2", (10, 0, 0), (10, 10, 0), "IPE400")

    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])

    p = a.get_part("MyPart")
    p.fem = p.to_fem_obj(2, "line")
    assert len(p.fem.sections) == 2

    name = "Merges_Sections"
    fem_format = FEATypes.SESAM

    a.to_fem(name, fem_format, overwrite=True, scratch_dir=tmp_path)

    inp_path = default_fem_inp_path(name, tmp_path)[fem_format]
    b = ada.from_fem(inp_path)
    pb = b.get_part("T1")

    assert len(pb.fem.elsets) == 12
    assert len(pb.fem.sections) == 10

    pb.fem.sections.merge_by_properties()

    assert len(pb.fem.sections) == 2
    assert len(pb.fem.elsets) == 4

    # b.to_fem("SectionsMerged", "abaqus", overwrite=True)


def test_merge_fem_sections2(tmp_path):
    bm1 = ada.Beam("Bm1", (0, 0, 0), (10, 0, 0), "IPE300")
    bm2 = ada.Beam("Bm2", (10, 0, 0), (10, 10, 0), "IPE300")
    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])

    p = a.get_part("MyPart")
    p.fem = p.to_fem_obj(2, "line")
    assert len(p.fem.sections) == 2

    name = "Merges_Sections_Same_Sections"
    fem_format = FEATypes.SESAM

    a.to_fem(name, fem_format, overwrite=True, scratch_dir=tmp_path)

    inp_path = default_fem_inp_path(name, tmp_path)[fem_format]
    b = ada.from_fem(inp_path)
    pb = b.get_part("T1")

    assert len(pb.fem.elsets) == 12
    assert len(pb.fem.sections) == 10

    pb.fem.sections.merge_by_properties()

    assert len(pb.fem.sections) == 2
    assert len(pb.fem.elsets) == 4

    # b.to_fem("SectionsMerged2", "abaqus", overwrite=True)


def test_merge_gen_beams(tmp_path):
    gp1 = GeneralProperties(
        Ax=0.00188594277,
        Ix=7.178e-08,
        Iy=6.1383e-06,
        Iz=1.2361e-07,
        Iyz=-4.5553e-07,
        Wxmin=4e-06,
        Wymin=5.62e-05,
        Wzmin=4.9e-06,
        Shary=0.0003924,
        Sharz=0.00103,
        Shceny=-0.0039015,
        Shcenz=-0.061909,
        Sy=4.76742207e-05,
        Sz=5.61828483e-06,
        Sfy=1,
        Sfz=1,
    )
    gp2 = GeneralProperties(
        Ax=0.2412,
        Ix=0.0180457793,
        Iy=0.0120546399,
        Iz=0.0180819593,
        Iyz=0.0,
        Wxmin=0.026934,
        Wymin=0.0172209,
        Wzmin=0.0172209,
        Shary=0.0357811,
        Sharz=0.0357811,
        Shceny=0.0,
        Shcenz=0.0,
        Sy=0.0101070004,
        Sz=0.0101070004,
        Sfy=1,
        Sfz=1,
    )
    bm1 = ada.Beam("Bm1", (0, 0, 0), (10, 0, 0), ada.Section("gp1", "GENBEAM", genprops=gp1))
    bm2 = ada.Beam("Bm2", (0, 5, 0), (10, 5, 0), ada.Section("gp2", "GENBEAM", genprops=gp2))

    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])

    p = a.get_part("MyPart")
    p.fem = p.to_fem_obj(2, "line")
    assert len(p.fem.sections) == 2

    name = "Merges_Sections_GenBeams"
    fem_format = FEATypes.SESAM

    a.to_fem(name, fem_format, overwrite=True, scratch_dir=tmp_path)

    inp_path = default_fem_inp_path(name, tmp_path)[fem_format]
    b = ada.from_fem(inp_path)
    pb = b.get_part("T1")

    assert len(pb.fem.elsets) == 12
    assert len(pb.fem.sections) == 10

    pb.fem.sections.merge_by_properties()

    assert len(pb.fem.sections) == 2
    assert len(pb.fem.elsets) == 4

    # b.to_fem("SectionsMerged3", "abaqus", overwrite=True)

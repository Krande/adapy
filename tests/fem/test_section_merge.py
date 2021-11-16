import pytest

import ada
from ada.fem.formats.utils import default_fem_inp_path


@pytest.fixture
def test_merge_sections_dir(test_dir):
    return test_dir / "merge_sections"


def test_merge_fem_sections(test_merge_sections_dir):
    bm1 = ada.Beam("Bm1", (0, 0, 0), (10, 0, 0), "IPE300")
    bm2 = ada.Beam("Bm2", (10, 0, 0), (10, 10, 0), "IPE400")
    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])

    p = a.get_part("MyPart")
    p.fem = p.to_fem_obj(2, "line")
    assert len(p.fem.sections) == 2

    name = "Merges_Sections"
    fem_format = "sesam"

    a.to_fem(name, fem_format, overwrite=True, scratch_dir=test_merge_sections_dir)

    inp_path = default_fem_inp_path(name, test_merge_sections_dir)[fem_format]
    b = ada.from_fem(inp_path)
    pb = b.get_part("T1")
    assert len(pb.fem.sections) == 10

    pb.fem.sections.merge_by_properties()

    assert len(pb.fem.sections) == 2

    # b.to_fem("SectionsMerged", "abaqus", overwrite=True)

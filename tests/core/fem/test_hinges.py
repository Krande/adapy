from ada import Beam, Part
from ada.fem import Csys
from ada.fem.conversion_utils import convert_hinges_2_couplings
from ada.fem.elements import Hinge, HingeProp


def test_simple_hinged_beam():
    bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE400")
    bm.connection_props.hinge_prop = HingeProp(end1=Hinge([1, 2, 3, 4, 6], Csys("MyBeam_hinge")))
    p = Part("MyPart") / bm
    p.fem = p.to_fem_obj(0.1)
    convert_hinges_2_couplings(p.fem)
    assert len(p.fem.constraints.values()) == 1

    # (Assembly() / p).to_fem("MyHingedBeam", "abaqus", overwrite=True, scratch_dir=test_dir)

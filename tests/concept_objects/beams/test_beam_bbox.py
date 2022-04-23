from ada import Assembly, Beam, Part, PrimBox
from ada.config import Settings

test_dir = Settings.test_dir / "beams"


def test_bbox_viz():

    blist = []
    ypos = 0

    for sec in ["IPE300", "HP200x10", "TUB300x30", "TUB300/200x20"]:
        bm = Beam(sec, (0, ypos, 0), (0, ypos, 1), sec)
        blist += [Part(sec + "_Z") / [bm, PrimBox("Bbox_Z_" + sec, *bm.bbox.minmax, colour="red", opacity=0.5)]]
        bm = Beam(sec, (0, ypos, 2), (1, ypos, 2), sec)
        blist += [Part(sec + "_X") / [bm, PrimBox("Bbox_X_" + sec, *bm.bbox.minmax, colour="red", opacity=0.5)]]
        bm = Beam("bm_" + sec + "_Y", (ypos, 0, 3), (ypos, 1, 3), sec)
        blist += [Part(sec + "_Y") / [bm, PrimBox("Bbox_Y_" + sec, *bm.bbox.minmax, colour="red", opacity=0.5)]]
        bm = Beam("bm_" + sec + "_XYZ", (ypos, ypos, 4), (ypos + 1, ypos + 1, 5), sec)
        blist += [Part(sec + "_XYZ") / [bm, PrimBox("Bbox_XYZ_" + sec, *bm.bbox.minmax, colour="red", opacity=0.5)]]
        ypos += 1
    a = Assembly() / blist
    _ = a.to_ifc(test_dir / "beam_bounding_box.ifc", return_file_obj=True)


def test_iprofiles_bbox():
    bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), "IPE300")
    assert bm.bbox.minmax == ((-0.075, -0.15, 0.0), (0.075, 0.15, 1.0))


def test_tubular_bbox():
    bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), "TUB300x30")
    assert bm.bbox.minmax == ((-0.3, -0.3, 0.0), (0.3, 0.3, 1.0))

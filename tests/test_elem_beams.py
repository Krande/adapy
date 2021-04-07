import unittest

from ada import Assembly, Beam, CurvePoly, Material, Part, Section
from ada.config import Settings
from ada.materials.metals import CarbonSteel

test_folder = Settings.test_dir / "beams"

# Rotational Relationships
section = "HP200x10"
angles = [0, 90, 180, 270]
vectorX = [(0, 0, 1), (0, -1, 0), (0, 0, -1), (0, 1, 0)]
vectorY = [(0, 0, 1), (1, 0, 0), (0, 0, -1), (-1, 0, 0)]
vectorZ = [(0, 1, 0), (-1, 0, 0), (0, -1, 0), (1, 0, 0)]


class BeamIO(unittest.TestCase):
    def test_beam_to_from_ifc(self):

        bm = Beam(
            "bm1",
            n1=[0, 0, 0],
            n2=[2, 0, 0],
            sec="IPE220",
            mat=Material("SteelMat", CarbonSteel("S420")),
            colour="red",
        )

        a = Assembly("MyAssembly") / [Part("MyPart") / bm]
        a.to_ifc(test_folder / "my_beam_profile.ifc")

        a2 = Assembly("MyNewAssembly")
        a2.read_ifc(test_folder / "my_beam_profile.ifc")

        # This would require more work put into __eq__ and __neq__. Not a priority (visual check in Blender for now)
        # bm2 = a2.get_by_name(bm.name)
        # assert bm2 == bm
        a2.to_ifc(test_folder / "my_beam_profile_re_exported.ifc")

    def test_beam_offset(self):
        bm1 = Beam(
            "bm1",
            n1=[0, 0, 0],
            n2=[2, 0, 0],
            sec="IPE300",
            mat=Material("SteelMat", CarbonSteel("S420")),
            colour="red",
            up=(0, 0, 1),
            e1=(0, 0, -0.1),
            e2=(0, 0, -0.1),
        )
        bm2 = Beam(
            "bm2",
            n1=[0, 0, 0],
            n2=[2, 0, 0],
            sec="IPE300",
            mat=Material("SteelMat", CarbonSteel("S420")),
            colour="blue",
            up=(0, 0, -1),
            e1=(0, 0, -0.1),
            e2=(0, 0, -0.1),
        )

        a = Assembly("Toplevel") / [Part("MyPart") / [bm1, bm2]]
        a.to_ifc(test_folder / "beams_offset.ifc")

    def test_beam_orientation(self):
        def create_ifc(name, up=(0, 0, 1)):
            a = Assembly("MyAssembly")
            p = Part(name)
            p.add_beam(
                Beam(
                    "bm_up",
                    n1=[0, 0, 0],
                    n2=[2, 0, 0],
                    sec="HP200x10",
                    mat=Material("SteelMat", CarbonSteel("S420")),
                    colour="red",
                    up=up,
                )
            )
            a.add_part(p)
            a.to_ifc(test_folder / name)

        create_ifc("hp_cardinal_up")
        create_ifc("hp_cardinal_down", (0, 0, -1))

        aup = Assembly("bm_up")
        aup.read_ifc(test_folder / "hp_cardinal_up.ifc")
        aup.read_ifc(test_folder / "hp_cardinal_down.ifc")

    def test_beam_rotation_by_angle(self):
        # Define beam rotations using vectors
        a = Assembly("AdaRotatedProfiles")
        p = Part("Part")
        a.add_part(p)

        for i, angle in enumerate(angles):
            # Along X-Axis
            bm = Beam(
                f"bmX_n{i}_a",
                n1=[0, 0, 0],
                n2=[5, 0, 0],
                sec=section,
                angle=angle,
                metadata=dict(props=dict(axis="X", angle=angle, vector=None)),
            )
            assert tuple(bm.up.tolist()) == vectorX[i]
            p.add_beam(bm)
            bm = Beam(
                f"bmX_n{i}_v",
                n1=[0, 0, 0],
                n2=[5, 0, 0],
                sec=section,
                up=vectorX[i],
                metadata=dict(props=dict(axis="X", angle=None, vector=vectorX[i])),
            )
            p.add_beam(bm)
            # Must fix error in 270 deg angle calculation
            # assert bm._angle == angle

            # Along Y-Axis
            bm = Beam(
                f"bmY_n{i}_a",
                n1=[0, 0, 0],
                n2=[0, 5, 0],
                sec=section,
                angle=angle,
                metadata=dict(props=dict(axis="Y", angle=angle, vector=None)),
            )
            p.add_beam(bm)
            assert tuple(bm.up.tolist()) == vectorY[i]
            bm = Beam(
                f"bmY_n{i}_v",
                n1=[0, 0, 0],
                n2=[0, 5, 0],
                sec=section,
                up=vectorY[i],
                metadata=dict(props=dict(axis="Y", angle=None, vector=vectorY[i])),
            )
            p.add_beam(bm)
            # assert bm._angle == angle

            # Along Z-Axis
            bm = Beam(
                f"bmZ_n{i}_a",
                n1=[0, 0, 0],
                n2=[0, 0, 5],
                sec=section,
                angle=angle,
                metadata=dict(props=dict(axis="Z", angle=angle, vector=None)),
            )
            p.add_beam(bm)
            assert tuple(bm.up.tolist()) == vectorZ[i]
            bm = Beam(
                f"bmZ_n{i}_v",
                n1=[0, 0, 0],
                n2=[0, 0, 5],
                sec=section,
                up=vectorZ[i],
                metadata=dict(props=dict(axis="Z", angle=None, vector=vectorZ[i])),
            )
            p.add_beam(bm)
            # assert bm._angle == angle

        p.to_stp(test_folder / "my_angle_rotated_profiles.stp")
        a.to_ifc(test_folder / "my_angle_rotated_profiles.ifc")

    def test_beam_directions(self):
        a = Assembly("AdaRotatedProfiles")
        p = Part("Part")
        a.add_part(p)

        props = dict(spre="/JSB_VA-DIN-SPEC/Y26IPE400", matr="/GR.355-II(Y26)_jsb_va")

        p.add_beam(
            Beam(
                "bm_test2X0",
                n1=[0, 0, 0],
                n2=[5, 0, 0],
                sec=section,
                angle=0,
                metadata=dict(props=props),
            )
        )
        p.add_beam(
            Beam(
                "bm_test2X90",
                n1=[0, 0, 1],
                n2=[5, 0, 1],
                sec=section,
                angle=90,
                metadata=dict(props=props),
            )
        )
        p.add_beam(
            Beam(
                "bm_test2Y0",
                n1=[0, 0, 2],
                n2=[0, 5, 2],
                sec=section,
                angle=0,
                metadata=dict(props=props),
            )
        )
        p.add_beam(
            Beam(
                "bm_test2Y90",
                n1=[0, 0, 3],
                n2=[0, 5, 3],
                sec=section,
                angle=90,
                metadata=dict(props=props),
            )
        )

        a.to_ifc(test_folder / "my_angled_profiles.ifc")

    def test_cone_beam(self):
        s_o = [(375.0, 375.0, 375.0), (375.0, -375.0, 375.0), (-375.0, -375.0, 375.0), (-375.0, 375.0, 375.0)]
        s_i = [(325.0, 325.0, 325.0), (-325.0, 325.0, 325.0), (-325.0, -325.0, 325.0), (325.0, -325.0, 325.0)]

        e_o = [(525.0, 525.0, 525.0), (525.0, -525.0, 525.0), (-525.0, -525.0, 525.0), (-525.0, 525.0, 525.0)]
        e_i = [(475.0, 475.0, 475.0), (-475.0, 475.0, 475.0), (-475.0, -475.0, 475.0), (475.0, -475.0, 475.0)]
        poly_s_o = CurvePoly(s_o, (0, 0, 0), (0, 0, 1), (1, 0, 0))
        poly_s_i = CurvePoly(s_i, (0, 0, 0), (0, 0, 1), (1, 0, 0))
        section_s = Section("MyStartCrossSection", "poly", outer_poly=poly_s_o, inner_poly=poly_s_i, units="mm")

        poly_e_o = CurvePoly(e_o, (0, 0, 0), (0, 0, 1), (1, 0, 0))
        poly_e_i = CurvePoly(e_i, (0, 0, 0), (0, 0, 1), (1, 0, 0))
        section_e = Section("MyEndCrossSection", "poly", outer_poly=poly_e_o, inner_poly=poly_e_i, units="mm")

        bm = Beam("MyCone", (2, 2, 2), (4, 4, 4), sec=section_s, tap=section_e)
        a = Assembly("Level1", project="Project0", creator="krande", units="mm") / (Part("Level2") / bm)
        a.to_ifc(test_folder / "cone_ex.ifc")

    # def test_revolved_beam(self):
    #     curve = CurveRevolve("THRU", (10, 0, 0), (10, 10, 0), point_on=(11, 5.0, 0.0), rot_axis=(0, 0, 1))
    #     # curve = None
    #     beam = Beam("MyBeam", sec="IPE600", curve=curve)
    #     a = Assembly("ExportedPlates", units="m")
    #     p = Part("MyPart")
    #     a.add_part(p)
    #
    #     p.add_beam(beam)
    #     a.to_ifc(test_folder / "my_curved_elem_m.ifc")
    #
    # def test_sweep_beam(self):
    #     curve = CurvePoly(points3d=[(10, 0, 0), (11, 5.0, 0.0), (10, 10, 0)])
    #     beam = Beam("MyBeam", sec="IPE600", curve=curve)
    #     a = Assembly("ExportedPlates", units="m")
    #     p = Part("MyPart")
    #     a.add_part(p)
    #
    #     p.add_beam(beam)
    #     a.to_ifc(test_folder / "my_swept_beam_elem_m.ifc")


class BeamProfiles(unittest.TestCase):
    """Test for profile interpretation"""

    def test_iprofiles(self):
        for sec in ["IPE300"]:
            bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
            assert isinstance(bm.section, Section)
            assert isinstance(bm.material, Material)

    def test_hea_profiles(self):
        for sec in ["HEA300"]:
            bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
            assert isinstance(bm.section, Section)
            assert isinstance(bm.material, Material)

    def test_heb_profiles(self):
        for sec in ["HEB300"]:
            bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
            assert isinstance(bm.section, Section)
            assert isinstance(bm.material, Material)

    def test_igirders(self):
        for sec in ["IG1200x600x20x30", "IG.1200x600x20x30"]:
            bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
            assert isinstance(bm.section, Section)
            assert isinstance(bm.material, Material)

    def test_box_girder(self):
        for sec in ["BGA.1000x400x20x30", "BG.1000x400x20x30"]:
            bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
            assert isinstance(bm.section, Section)
            assert isinstance(bm.material, Material)

    def test_hp_profile(self):
        for sec in ["HP200x10"]:
            bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
            assert isinstance(bm.section, Section)
            assert isinstance(bm.material, Material)

    def test_tub_profile(self):
        validation = [dict(r=0.3, wt=0.04)]
        for i, sec in enumerate(["PIPE300x40"]):
            bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
            assert isinstance(bm.section, Section)
            assert isinstance(bm.material, Material)
            assert validation[i]["r"] == bm.section.r
            assert validation[i]["wt"] == bm.section.wt

    def test_circ_profile(self):
        validation = [dict(r=0.125)]
        for i, sec in enumerate(["CIRC125"]):
            bm = Beam("my_beam", (0, 0, 0), (1, 1, 1), sec)
            assert isinstance(bm.section, Section)
            assert isinstance(bm.material, Material)
            assert validation[i]["r"] == bm.section.r

    def test_tapered_profile(self):
        bm = Beam("MyTaperedBeam", (0, 0, 0), (1, 1, 1), "TUB300/200x20")
        a = Assembly() / (Part("Test") / bm)
        a.to_ifc(test_folder / "tapered.ifc")


class BeamBBox(unittest.TestCase):
    """Test for profile interpretation"""

    def test_bbox_viz(self):
        from ada import PrimBox

        blist = []
        ypos = 0

        for sec in ["IPE300", "HP200x10", "TUB300x30", "TUB300/200x20"]:
            bm = Beam(sec, (0, ypos, 0), (0, ypos, 1), sec)
            blist += [Part(sec + "_Z") / [bm, PrimBox("Bbox_Z_" + sec, *bm.bbox, colour="red", opacity=0.5)]]
            bm = Beam(sec, (0, ypos, 2), (1, ypos, 2), sec)
            blist += [Part(sec + "_X") / [bm, PrimBox("Bbox_X_" + sec, *bm.bbox, colour="red", opacity=0.5)]]
            bm = Beam("bm_" + sec + "_Y", (ypos, 0, 3), (ypos, 1, 3), sec)
            blist += [Part(sec + "_Y") / [bm, PrimBox("Bbox_Y_" + sec, *bm.bbox, colour="red", opacity=0.5)]]
            bm = Beam("bm_" + sec + "_XYZ", (ypos, ypos, 4), (ypos + 1, ypos + 1, 5), sec)
            blist += [Part(sec + "_XYZ") / [bm, PrimBox("Bbox_XYZ_" + sec, *bm.bbox, colour="red", opacity=0.5)]]
            ypos += 1
        a = Assembly() / blist
        a.to_ifc(test_folder / "beam_bounding_box.ifc")

    def test_iprofiles_bbox(self):
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), "IPE300")
        assert bm.bbox == ((-0.075, -0.15, 0.0), (0.075, 0.15, 1.0))

    def test_tubular_bbox(self):
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), "TUB300x30")
        assert bm.bbox == ((-0.3, -0.3, 0.0), (0.3, 0.3, 1.0))


if __name__ == "__main__":
    unittest.TestCase()

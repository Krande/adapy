import unittest

from ada import Assembly, Beam, Part, Pipe, Plate, Section, Wall
from ada.config import Settings
from ada.core.utils import download_to
from ada.param_models.basic_module import SimpleStru
from ada.param_models.basic_structural_components import Door, Window

test_folder = Settings.test_dir / "ifc_basics"


class IfcExport(unittest.TestCase):
    def test_export_basic(self):
        bm = Beam(
            "MyBeam",
            (0, 0, 0),
            (2, 0, 0),
            Section("MySec", from_str="BG300x200x10x20"),
            metadata=dict(hidden=True),
        )

        a = Assembly("MyFirstIfcFile") / (Part("MyBldg", metadata=dict(ifctype="building")) / bm)
        a.to_ifc(test_folder / "MyTest.ifc")

    def test_export_layers(self):
        bm = Beam(
            "MyBeam",
            (0, 0, 0),
            (2, 0, 0),
            Section("MySec", from_str="BG300x200x10x20"),
            metadata=dict(hidden=True),
        )

        webh = bm.section.h - bm.section.t_fbtn * 2

        pl1 = Plate(
            "Web1",
            [(0, 0), (2, 0), (2, webh), (0, webh)],
            bm.section.t_w,
            origin=(0, -bm.section.w_btn / 2 + bm.section.t_w, -webh / 2),
            normal=(0, -1, 0),
            xdir=(1, 0, 0),
        )

        pl2 = Plate(
            "Web2",
            [(0, 0), (2, 0), (2, webh), (0, webh)],
            bm.section.t_w,
            origin=(0, bm.section.w_btn / 2, -webh / 2),
            normal=(0, -1, 0),
            xdir=(1, 0, 0),
        )

        pl3 = Plate(
            "Fla1",
            [(0, 0), (2, 0), (2, bm.section.w_top), (0, bm.section.w_top)],
            bm.section.t_fbtn,
            origin=(0, -bm.section.w_btn / 2, -bm.section.h / 2),
            normal=(0, 0, 1),
            xdir=(1, 0, 0),
        )

        pl4 = Plate(
            "Fla2",
            [(0, 0), (2, 0), (2, bm.section.w_top), (0, bm.section.w_top)],
            bm.section.t_fbtn,
            origin=(0, -bm.section.w_btn / 2, bm.section.h / 2 - bm.section.t_fbtn),
            normal=(0, 0, 1),
            xdir=(1, 0, 0),
        )
        p = Part("MyBldg", metadata=dict(ifctype="building"))
        a = Assembly("MySite", project="MyLayersProject") / [p / [bm, pl1, pl2, pl3, pl4]]

        ifc_name = "MyLayerTest.ifc"
        a.to_ifc(test_folder / ifc_name)

        b = Assembly("MyImportedLayers")
        b.read_ifc(test_folder / ifc_name)

    def test_to_ifc(self):
        bm1 = Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red")
        bm2 = Beam("bm2", n1=[0, 0, 0], n2=[0, 2, 0], sec="IPE220", colour="blue")
        bm3 = Beam("bm3", n1=[0, 0, 0], n2=[0, 0, 2], sec="IPE220", colour="green")
        bm4 = Beam("bm4", n1=[0, 0, 0], n2=[2, 0, 2], sec="IPE220", colour="black")
        bm5 = Beam("bm5", n1=[0, 0, 2], n2=[2, 0, 2], sec="IPE220", colour="white")

        pl1 = Plate(
            "pl1",
            [(0, 0, 0), (0, 0, 2), (0, 2, 2), (0, 2.0, 0.0)],
            0.01,
            use3dnodes=True,
        )

        a = Assembly("MySite") / [Part("MyBuilding") / [bm1, bm2, bm3, bm4, bm5, pl1]]

        a.to_ifc(test_folder / "my_test.ifc")

    def test_ifc_groups(self):
        a = Assembly("MySiteName", project="MyTestProject")
        p = Part(
            "MyTopSpatialLevel",
            metadata=dict(ifctype="spatial", description="MyTopLevelSpace"),
        )
        p.add_beam(Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red"))
        a.add_part(p)

        newp = Part(
            "MySecondLevel",
            metadata=dict(ifctype="spatial", description="MySecondLevelSpace"),
        )
        newp.add_beam(Beam("bm2", n1=[0, 0, 0], n2=[0, 2, 0], sec="IPE220", colour="blue"))
        p.add_part(newp)

        newp2 = Part(
            "MyThirdLevel",
            metadata=dict(ifctype="spatial", description="MyThirdLevelSpace"),
        )
        newp2.add_beam(Beam("bm3", n1=[0, 0, 0], n2=[0, 0, 2], sec="IPE220", colour="green"))
        newp2.add_plate(
            Plate(
                "pl1",
                [(0, 0, 0), (0, 0, 2), (0, 2, 2), (0, 2.0, 0.0)],
                0.01,
                use3dnodes=True,
            )
        )
        newp.add_part(newp2)

        a.to_ifc(test_folder / "my_test_groups.ifc")

    def test_profiles_to_ifc(self):
        a = Assembly("MyAssembly")
        p = Part("MyPart")
        p.add_beam(Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red"))
        p.add_beam(Beam("bm2", n1=[0, 0, 1], n2=[2, 0, 1], sec="HP220x10", colour="blue"))
        p.add_beam(Beam("bm3", n1=[0, 0, 2], n2=[2, 0, 2], sec="BG800x400x20x40", colour="green"))
        p.add_beam(Beam("bm4", n1=[0, 0, 3], n2=[2, 0, 3], sec="CIRC200", colour="green"))
        p.add_beam(Beam("bm5", n1=[0, 0, 4], n2=[2, 0, 4], sec="TUB200x10", colour="green"))
        a.add_part(p)
        a.to_ifc(test_folder / "my_beam_profiles.ifc")


class IfcRoundtripping(unittest.TestCase):
    def test_ifc_roundtrip(self):
        a = Assembly("my_test_assembly")
        a.add_part(SimpleStru("my_simple_stru"))
        a.to_ifc(test_folder / "my_test.ifc")

        b = Assembly("MyReImport")
        b.read_ifc(test_folder / "my_test.ifc")
        b.to_ifc(test_folder / "my_test_re_exported.ifc")

        all_parts = b.get_all_parts_in_assembly()
        assert len(all_parts) == 3

    def test_ifc_reimport(self):

        # Model to be re-imported
        a = Assembly("my_test_assembly")
        a.add_part(SimpleStru("my_simple_stru"))
        a.to_ifc(test_folder / "my_exported_param_model.ifc")

        points = [(0, 0, 0), (5, 0, 0), (5, 5, 0)]
        w = Wall("MyWall", points, 3, 0.15, offset="LEFT")
        wi = Window("MyWindow1", 1.5, 1, 0.15)
        wi2 = Window("MyWindow2", 2, 1, 0.15)
        door = Door("Door1", 1.5, 2, 0.2)
        w.add_insert(wi, 0, 1, 1.2)
        w.add_insert(wi2, 1, 1, 1.2)
        w.add_insert(door, 0, 3.25, 0)

        a = Assembly("MyTest")
        p = Part("MyPart")
        a.add_part(p)
        p.add_elements_from_ifc(test_folder / "my_exported_param_model.ifc")
        p.add_wall(w)

        z = 3.2
        y0 = -200e-3
        x0 = -y0
        pipe1 = Pipe(
            "Pipe1",
            [
                (0, y0, z),
                (5 + x0, y0, z),
                (5 + x0, y0 + 5, z),
                (10, y0 + 5, z + 2),
                (10, y0 + 5, z + 10),
            ],
            Section("PSec1", "PIPE", r=0.10, wt=5e-3),
        )
        p.add_pipe(pipe1)
        a.to_ifc(test_folder / "my_reimport_of_elements.ifc")


url_root = "https://raw.githubusercontent.com/buildingSMART/Sample-Test-Files/"


class IfcExternal(unittest.TestCase):
    def test_import_arcboundary(self):

        url = url_root + "master/IFC%204.0/NURBS/Bentley%20Building%20Designer/SolidsAndSheets/WithArcBoundary.ifc"
        dest = "c:/temp/ifc_files/WithArcBoundary.ifc"
        download_to(dest, url)

        a = Assembly("MyAssembly")
        a.read_ifc(dest)


if __name__ == "__main__":
    unittest.main()

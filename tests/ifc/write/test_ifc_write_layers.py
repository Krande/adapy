import ada
from ada import Assembly, Beam, Part, Placement, Plate, Section
from ada.config import Settings

test_dir = Settings.test_dir / "ifc_layers"


def test_export_layers():
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
        placement=Placement(
            origin=(0, -bm.section.w_btn / 2 + bm.section.t_w, -webh / 2), zdir=(0, -1, 0), xdir=(1, 0, 0)
        ),
    )

    pl2 = Plate(
        "Web2",
        [(0, 0), (2, 0), (2, webh), (0, webh)],
        bm.section.t_w,
        placement=Placement(origin=(0, bm.section.w_btn / 2, -webh / 2), zdir=(0, -1, 0), xdir=(1, 0, 0)),
    )

    pl3 = Plate(
        "Fla1",
        [(0, 0), (2, 0), (2, bm.section.w_top), (0, bm.section.w_top)],
        bm.section.t_fbtn,
        placement=Placement(origin=(0, -bm.section.w_btn / 2, -bm.section.h / 2), zdir=(0, 0, 1), xdir=(1, 0, 0)),
    )

    pl4 = Plate(
        "Fla2",
        [(0, 0), (2, 0), (2, bm.section.w_top), (0, bm.section.w_top)],
        bm.section.t_fbtn,
        placement=Placement(
            origin=(0, -bm.section.w_btn / 2, bm.section.h / 2 - bm.section.t_fbtn),
            zdir=(0, 0, 1),
            xdir=(1, 0, 0),
        ),
    )
    p = Part("MyBldg")
    a = Assembly("MySite", project="MyLayersProject") / (p / [bm, pl1, pl2, pl3, pl4])

    ifc_name = "MyLayerTest.ifc"
    fp = a.to_ifc(test_dir / ifc_name, file_obj_only=True)
    print(a)
    b = ada.from_ifc(fp)
    print(b)

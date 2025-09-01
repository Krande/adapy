from ada import Assembly, Beam, Part, Pipe, Plate, Section, Wall
from ada.param_models.basic_module import SimpleStru
from ada.param_models.basic_structural_components import Door, Window


def test_meter_to_millimeter():
    p = Part(
        "MyTopSpatialLevel",
        metadata=dict(ifctype="storey", description="MyTopLevelSpace"),
    )
    bm1 = Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", color="red")

    p2 = Part(
        "MySecondLevel",
        metadata=dict(ifctype="storey", description="MySecondLevelSpace"),
    )
    bm2 = Beam("bm2", n1=[0, 0, 0], n2=[0, 2, 0], sec="IPE220", color="blue")

    p3 = Part(
        "MyThirdLevel",
        metadata=dict(ifctype="storey", description="MyThirdLevelSpace"),
    )
    bm3 = Beam("bm3", n1=[0, 0, 0], n2=[0, 0, 2], sec="IPE220", color="green")
    pl1 = Plate.from_3d_points(
        "pl1",
        [(0, 0, 0), (0, 0, 2), (0, 2, 2), (0, 2.0, 0.0)],
        0.01,
    )

    a = Assembly("MySiteName", project="MyTestProject") / [p / [bm1, p2 / [bm2, p3 / [bm3, pl1]]]]

    a.units = "mm"
    assert tuple(bm3.n2.p) == (0, 0, 2000)
    assert pl1.t == 10


def test_ifc_reimport():
    # Model to be re-imported
    a = Assembly("my_test_assembly") / SimpleStru("my_simple_stru")
    fp = a.to_ifc(file_obj_only=True)

    points = [(0, 0, 0), (5, 0, 0), (5, 5, 0)]
    w = Wall("MyWall", points, 3, 0.15, offset="LEFT")
    wi = Window("MyWindow1", 1.5, 1, 0.15)
    wi2 = Window("MyWindow2", 2, 1, 0.15)
    door = Door("Door1", 1.5, 2, 0.2)
    w.add_insert(wi, 0, 1, 1.2)
    w.add_insert(wi2, 1, 1, 1.2)
    w.add_insert(door, 0, 3.25, 0)

    p = Part("MyPart")

    p.add_elements_from_ifc(fp)
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

    b = Assembly("MyTest") / p

    # b.show()

    b.units = "mm"
    f_mm = b.to_ifc(file_obj_only=True, validate=False)
    f_mm_str = f_mm.wrapped_data.to_string()

    # b.show()

    b.units = "m"
    f_m = b.to_ifc(file_obj_only=True, validate=True)
    f_m_str = f_m.wrapped_data.to_string()

    # b.show()

    # Check that unit definitions are present as expected
    # For millimeter units (prefix .MILLI.)
    assert "IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)" in f_mm_str
    assert "IFCSIUNIT(*,.AREAUNIT.,.MILLI.,.SQUARE_METRE.)" in f_mm_str
    assert "IFCSIUNIT(*,.VOLUMEUNIT.,.MILLI.,.CUBIC_METRE.)" in f_mm_str

    # For meter units (no prefix -> represented as *)
    assert "IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)" in f_m_str
    assert "IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.)" in f_m_str
    assert "IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.)" in f_m_str

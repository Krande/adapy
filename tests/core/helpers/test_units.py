from ada import Assembly, Beam, Part, Pipe, Plate, Section, Wall
from ada.param_models.basic_module import SimpleStru
from ada.param_models.basic_structural_components import Door, Window


def test_meter_to_millimeter(tmp_path):
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

    # a.to_ifc(test_units_dir / "my_test_in_meter.ifc")

    a.units = "mm"
    assert tuple(bm3.n2.p) == (0, 0, 2000)
    assert pl1.t == 10
    # a.to_ifc(test_units_dir / "my_test_in_millimeter.ifc")


def test_ifc_reimport(tmp_path):
    # Model to be re-imported
    a = Assembly("my_test_assembly") / SimpleStru("my_simple_stru")
    fp = a.to_ifc(tmp_path / "my_exported_param_model.ifc", file_obj_only=True)

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

    b.units = "mm"
    b.to_ifc(tmp_path / "my_reimport_of_elements_mm.ifc", file_obj_only=True, validate=False)
    # TODO: Re-import is still not supported. Should look into same approach as BlenderBIM by
    #       only communicating and updating the ifcopenshell file object.
    b.units = "m"
    b.to_ifc(tmp_path / "my_reimport_of_elements_m.ifc", file_obj_only=False, validate=True)

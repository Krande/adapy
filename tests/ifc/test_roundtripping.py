import ada
from ada import Assembly, Part, Pipe, Section, Wall
from ada.config import Settings
from ada.param_models.basic_module import SimpleStru
from ada.param_models.basic_structural_components import Door, Window

test_dir = Settings.test_dir / "ifc_basics"


def test_ifc_roundtrip():
    a = ada.Assembly("my_test_assembly") / SimpleStru("my_simple_stru")
    fp = a.to_ifc(test_dir / "my_test.ifc", file_obj_only=True)

    b = ada.from_ifc(fp)
    _ = b.to_ifc(test_dir / "my_test_re_exported.ifc", file_obj_only=True)

    all_parts = b.get_all_parts_in_assembly()
    assert len(all_parts) == 3


def test_ifc_external_elements_import():
    # Model to be re-imported
    a0 = Assembly("my_test_assembly") / SimpleStru("my_simple_stru")
    fp = a0.to_ifc(test_dir / "my_exported_param_model.ifc", file_obj_only=True)

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

    _ = a.to_ifc(test_dir / "my_reimport_of_elements.ifc", file_obj_only=True)

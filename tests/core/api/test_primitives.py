import ada
from ada.api.primitives.primitive_face import PrimFace
from ada.geom.surfaces import CurveBoundedPlane

def test_export_primitives(tmp_path):
    ifc_file = tmp_path / "world_of_shapes.ifc"

    a = ada.Assembly("Site") / [
        ada.PrimBox("VolBox", (0.2, 0.2, 2), (1.2, 1.2, 4)),
        ada.PrimCyl("VolCyl", (2, 2, 2), (4, 4, 4), 0.2),
        ada.PrimExtrude("VolExtrude", [(0, 0), (1, 0), (0.5, 1)], 2, (0, 0, 1), (2, 2, 2), (1, 0, 0)),
        ada.PrimRevolve(
            "VolRevolve",
            points=[(0, 0), (1, 0), (0.5, 1)],
            origin=(2, 2, 3),
            xdir=(0, 0, 1),
            normal=(1, 0, 0),
            rev_angle=275,
        ),
    ]
    fp = a.to_ifc(ifc_file, file_obj_only=True)

    b = ada.from_ifc(fp)
    assert len(b.shapes) == 4
    print(b)


def test_sweep_shape():
    sweep_curve = [(0, 0, 0), (5, 5.0, 0.0, 1), (10, 0, 0)]
    ot = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
    shape = ada.PrimSweep("MyShape", sweep_curve, ot)

    a = ada.Assembly("SweptShapes", units="m") / [ada.Part("MyPart") / [shape]]
    _ = a.to_ifc(file_obj_only=True)


def test_prim_face():
    ps_z = PrimFace("my_face_z", [(0, 0), (1, 0), (1, 1), (0, 1)], (0, 0, 1), (0, 0, 0))
    ps_y = PrimFace("my_face_y", [(0, 0), (1, 0), (1, 1), (0, 1)], (0, 1, 0), (0, 0, 0))
    ps_x = PrimFace("my_face_x", [(0, 0), (1, 0), (1, 1), (0, 1)], (1, 0, 0), (0, 0, 0), xdir=(0, 0, -1))

    assert isinstance(ps_x.solid_geom().geometry, CurveBoundedPlane)
    assert isinstance(ps_y.solid_geom().geometry, CurveBoundedPlane)
    assert isinstance(ps_z.solid_geom().geometry, CurveBoundedPlane)

    # p = ada.Part("MyAssembly") / (ps_z, ps_y, ps_x)
    # p.show(stream_from_ifc_store=False)

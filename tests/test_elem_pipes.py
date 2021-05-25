import unittest

from ada import Assembly, Part, Pipe, Section
from ada.config import Settings
import logging

logging.basicConfig(level=logging.DEBUG)

test_folder = Settings.test_dir / "pipes"
z = 3.2
y0 = -200e-3
x0 = -y0


class PipeIO(unittest.TestCase):
    def test_pipe_straight(self):
        a = Assembly("MyTest")

        p = Part("MyPart")
        a.add_part(p)
        z = 3.2
        y0 = -200e-3
        pipe1 = Pipe("Pipe1", [(0, y0, 0), (0, y0, z)], Section("PSec", "PIPE", r=0.10, wt=5e-3))
        p.add_pipe(pipe1)
        a.to_ifc(test_folder / "pipe_straight.ifc")
        a._repr_html_()

    def test_pipe_bend(self):
        a = Assembly("MyTest")
        p = a.add_part(Part("MyPart"))

        pipe1 = Pipe(
            "Pipe1",
            [
                (0, y0, z),
                (5 + x0, y0, z),
                (5 + x0, y0 + 5, z),
                (10, y0 + 5, z + 2),
                (10, y0 + 5, z + 10),
            ],
            Section("PSec", "PIPE", r=0.10, wt=5e-3),
        )
        p.add_pipe(pipe1)
        a.to_ifc(test_folder / "pipe_bend.ifc")
        # a.to_stp(test_folder / "pipe_bend.stp")
        # a._repr_html_()

    def test_ifc_elbow(self):
        from ada.core.ifc_utils import create_ifcaxis2placement, create_ifclocalplacement, create_guid
        from ada.core.utils import normal_to_points_in_plane, get_center_from_3_points_and_radius
        from ada.core.constants import X, Y, Z, O

        p1 = (0, y0, z)
        p2 = (5 + x0, y0, z)
        p3 = (5 + x0, y0 + 5, z)
        sec = Section("PSec", "PIPE", r=0.10, wt=5e-3)
        pi1 = Pipe("pipe1", [p1, p2], sec)
        pi2 = Pipe("pipe2", [p2, p3], sec)
        a = Assembly("MyTest") / (Part("MyPart") / [pi1, pi2])
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = f.by_type("IfcOwnerHistory")[0]
        schema = a.ifc_file.wrapped_data.schema

        center, _, _, _ = get_center_from_3_points_and_radius(p1, p2, p3, 0.193)

        opening_axis_placement = create_ifcaxis2placement(f, O, Z, X)

        profile = sec.ifc_profile
        normal = normal_to_points_in_plane([p1, p2, p3])
        revolve_axis = center + normal
        revolve_angle = 10

        ifcorigin = f.createIfcCartesianPoint(p1)
        ifcaxis1dir = f.createIfcAxis1Placement(ifcorigin, f.createIfcDirection(revolve_axis.astype(float).tolist()))

        ifc_shape = f.createIfcRevolvedAreaSolid(profile, opening_axis_placement, ifcaxis1dir, revolve_angle)

        curve = f.createIfcTrimmedCurve()
        body = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [ifc_shape])
        axis = f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [curve])
        prod_def_shp = f.createIfcProductDefinitionShape(None, None, (axis, body))

        pfitting_placement = create_ifclocalplacement(f, O, Z, X)

        pfitting = f.createIfcBuildingElementProxy(
            # pfitting = f.createIfcPipeFitting(
            create_guid(),
            owner_history,
            "MyManuel Elbow",
            "An awesome Elbow",
            None,
            pfitting_placement,
            prod_def_shp,
            None,
            None,
        )


if __name__ == "__main__":
    unittest.main()

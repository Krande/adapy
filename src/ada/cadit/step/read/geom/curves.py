from OCC.Core.BRep import BRep_Tool
from OCC.Core.Geom import Geom_BSplineCurve, Geom_Circle, Geom_Line, Geom_Surface
from OCC.Core.gp import gp_Dir, gp_Pnt
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_WIRE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Wire

from ada import Direction
from ada.cadit.step.read.geom.helpers import (
    array1_to_int_list,
    array1_to_list,
    array1_to_point_list,
)
from ada.geom import curves as geo_cu
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def get_wires_from_face(face: TopoDS_Face, surface: Geom_Surface) -> list[geo_cu.CURVE_GEOM_TYPES]:
    # Retrieve outer and inner boundaries (wires)
    wire_explorer = TopExp_Explorer(face, TopAbs_WIRE)
    wires = []
    while wire_explorer.More():
        wire: TopoDS_Wire = wire_explorer.Current()
        print("Found boundary wire (outer or inner loop).")

        # Process the wire (could be outer or inner boundary)
        wire = process_wire(wire, surface)
        if wire:
            wires.append(wire)
        else:
            raise NotImplementedError("Failed to retrieve boundary curves from wire.")
        wire_explorer.Next()

    return wires


# Function to process the wire and retrieve the boundary curves
def process_wire(wire: TopoDS_Wire, surface: Geom_Surface) -> list[geo_cu.CURVE_GEOM_TYPES]:
    # Here, explore the edges in the wire
    curves = []
    edge_explorer = TopExp_Explorer(wire, TopAbs_EDGE)
    while edge_explorer.More():
        edge = edge_explorer.Current()

        # Analyze the edge geometry (e.g., BSpline curve, lines, etc.)
        edge_curve = BRep_Tool.Curve(edge)
        if edge_curve:
            curve_handle, first, last = BRep_Tool.Curve(edge)
            # print(f"Processing edge from {first} to {last}.")

            # Here you can check if the edge is a B-spline or other curve type
            # and process accordingly
            if curve_handle.DynamicType().Name() == "Geom_BSplineCurve":
                # Extract B-spline curve parameters
                bspline_curve = Geom_BSplineCurve.DownCast(curve_handle)

                degree = bspline_curve.Degree()
                poles = array1_to_point_list(bspline_curve.Poles())
                knots = array1_to_list(bspline_curve.Knots())
                mults = array1_to_int_list(bspline_curve.Multiplicities())
                closed = bool(bspline_curve.IsClosed())
                # There is no direct mapping for curve form / knot spec from OCC here; use UNSPECIFIED defaults
                curve_form = geo_cu.BSplineCurveFormEnum.UNSPECIFIED
                knot_spec = geo_cu.KnotType.UNSPECIFIED
                self_intersect = False

                if bspline_curve.IsRational():
                    weights = array1_to_list(bspline_curve.Weights())
                    curve = geo_cu.RationalBSplineCurveWithKnots(
                        degree=degree,
                        control_points_list=poles,
                        curve_form=curve_form,
                        closed_curve=closed,
                        self_intersect=self_intersect,
                        knot_multiplicities=mults,
                        knots=knots,
                        knot_spec=knot_spec,
                        weights_data=weights,
                    )
                else:
                    curve = geo_cu.BSplineCurveWithKnots(
                        degree=degree,
                        control_points_list=poles,
                        curve_form=curve_form,
                        closed_curve=closed,
                        self_intersect=self_intersect,
                        knot_multiplicities=mults,
                        knots=knots,
                        knot_spec=knot_spec,
                    )

            elif curve_handle.DynamicType().Name() == "Geom_Line":
                line_curve: Geom_Line = Geom_Line.DownCast(curve_handle)
                line_curve: Geom_Line

                # Process the line geometry
                line_pos = line_curve.Position()
                o: gp_Pnt = line_pos.Location()
                d: gp_Dir = line_pos.Direction()

                curve = geo_cu.Line(pnt=Point(o.X(), o.Y(), o.Z()), dir=Direction(d.X(), d.Y(), d.Z()))
            elif curve_handle.DynamicType().Name() == "Geom_Circle":
                circle = Geom_Circle.DownCast(curve_handle)
                pos = circle.Position()
                o: gp_Pnt = pos.Location()
                axis_dir: gp_Dir = pos.Direction()
                x_dir: gp_Dir = pos.XDirection()

                placement = Axis2Placement3D(
                    location=Point(o.X(), o.Y(), o.Z()),
                    axis=Direction(axis_dir.X(), axis_dir.Y(), axis_dir.Z()),
                    ref_direction=Direction(x_dir.X(), x_dir.Y(), x_dir.Z()),
                )
                curve = geo_cu.Circle(position=placement, radius=circle.Radius())
            else:
                raise NotImplementedError(f"Edge geometry type {curve_handle.DynamicType().Name()} not implemented.")
            curves.append(curve)
        edge_explorer.Next()

    return curves

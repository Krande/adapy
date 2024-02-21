import numpy as np

from ada.core.utils import roundoff


def import_indexedpolycurve(ipoly, normal, xdir, origin):
    """

    :param ipoly: IFC element
    :param normal:
    :param xdir:
    :param origin:
    :return:
    """
    from ada import ArcSegment, LineSegment
    from ada.core.curve_utils import segments_to_local_points
    from ada.core.vector_transforms import global_2_local_nodes

    ydir = np.cross(normal, xdir)
    nodes3d = [p for p in ipoly.Points.CoordList]
    nodes2d = global_2_local_nodes([xdir, ydir], origin, nodes3d)
    nodes2d = [np.array([n[0], n[1], 0.0]) for n in nodes2d]
    seg_list = []
    for i, seg in enumerate(ipoly.Segments):
        if seg.is_a("IfcLineIndex"):
            v = seg.wrappedValue
            p1 = nodes2d[v[0] - 1]
            p2 = nodes2d[v[1] - 1]
            seg_list.append(LineSegment(p1=p1, p2=p2))
        elif seg.is_a("IfcArcIndex"):
            v = seg.wrappedValue
            p1 = nodes2d[v[0] - 1]
            p2 = nodes2d[v[1] - 1]
            p3 = nodes2d[v[2] - 1]
            seg_list.append(ArcSegment(p1, p3, midpoint=p2))
        else:
            raise ValueError("Unrecognized type")

    local_points = [(roundoff(x[0]), roundoff(x[1])) for x in segments_to_local_points(seg_list)]
    return local_points


def import_polycurve(poly, normal, xdir):
    from ada.core.vector_transforms import global_2_local_nodes

    ydir = np.cross(normal, xdir)
    nodes3d = [p for p in poly.Points]
    nodes2d = global_2_local_nodes([xdir, ydir], (0, 0, 0), nodes3d)

    return nodes2d

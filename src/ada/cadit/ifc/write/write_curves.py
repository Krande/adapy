from ada.api.curves import CurvePoly2d


def write_curve_poly(curve: CurvePoly2d):
    a = curve.parent.parent.get_assembly()
    f = a.ifc_store.f

    ifc_segments = []
    for seg_ind in curve.seg_index:
        if len(seg_ind) == 2:
            ifc_segments.append(f.createIfcLineIndex(seg_ind))
        elif len(seg_ind) == 3:
            ifc_segments.append(f.createIfcArcIndex(seg_ind))
        else:
            raise ValueError("Unrecognized number of values")

    # TODO: Investigate using 2DLists instead is it could reduce complexity?
    points = [tuple(x.astype(float).tolist()) for x in curve.seg_global_points]
    ifc_point_list = f.createIfcCartesianPointList3D(points)
    segindex = f.createIfcIndexedPolyCurve(ifc_point_list, ifc_segments, False)
    return segindex

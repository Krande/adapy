import math

from OCC.Core import Precision
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepTools import breptools_Clean, breptools_WriteToString
from OCC.Core.IMeshTools import IMeshTools_Parameters
from OCC.Core.TopoDS import TopoDS_Shape


def serialize_shape(shape: TopoDS_Shape) -> str:
    breptools_Clean(shape)
    return breptools_WriteToString(shape)


def tesselate_shape(shp, line_defl: float = None, angle_def: float = 20) -> int:
    """See https://dev.opencascade.org/doc/overview/html/occt_user_guides__mesh.html#occt_modalg_11_2"""
    breptools_Clean(shp)

    mesh_params = IMeshTools_Parameters()
    min_size = Precision.precision.Confusion()
    mesh_params.MinSize = min_size
    if line_defl is not None:
        mesh_params.Deflection = line_defl

    if angle_def is not None:
        mesh_params.Angle = angle_def * math.pi / 180

    mesh_params.InParallel = True

    msh_algo = BRepMesh_IncrementalMesh(shp, mesh_params)

    # Triangulate
    msh_algo.Perform()

    status = msh_algo.GetStatusFlags()
    return status

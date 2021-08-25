import math
import pathlib

from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCC.Extend.DataExchange import read_step_file
from OCC.Extend.TopologyUtils import TopologyExplorer


def extract_shapes(step_path, scale, transform, rotate):
    from OCC.Extend.DataExchange import read_step_file

    shapes = []

    cad_file_path = pathlib.Path(step_path)
    if cad_file_path.is_file():
        shapes += extract_subshapes(read_step_file(str(cad_file_path)))
    elif cad_file_path.is_dir():
        shapes += walk_shapes(cad_file_path)
    else:
        raise Exception(f'step_ref "{step_path}" does not represent neither file or folder found on system')

    shapes = [transform_shape(s, scale, transform, rotate) for s in shapes]
    return shapes


def transform_shape(shp_, scale, transform, rotate):
    trsf = gp_Trsf()
    if scale is not None:
        trsf.SetScaleFactor(scale)
    if transform is not None:
        trsf.SetTranslation(gp_Vec(transform[0], transform[1], transform[2]))
    if rotate is not None:
        pt = gp_Pnt(rotate[0][0], rotate[0][1], rotate[0][2])
        dire = gp_Dir(rotate[1][0], rotate[1][1], rotate[1][2])
        revolve_axis = gp_Ax1(pt, dire)
        trsf.SetRotation(revolve_axis, math.radians(rotate[2]))
    return BRepBuilderAPI_Transform(shp_, trsf, True).Shape()


def walk_shapes(dir_path):
    from ..core.utils import get_list_of_files

    shps = []
    for stp_file in get_list_of_files(dir_path, ".stp"):
        shps += extract_subshapes(read_step_file(stp_file))
    return shps


def extract_subshapes(shp_):
    s = []
    t = TopologyExplorer(shp_)
    for solid in t.solids():
        s.append(solid)
    return s

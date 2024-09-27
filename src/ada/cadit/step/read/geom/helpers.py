from OCC.Core.gp import gp_Pnt
from OCC.Core.TColgp import TColgp_Array2OfPnt
from OCC.Core.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal

from ada import Point


def array1_to_list(array1: TColStd_Array1OfReal) -> list[float]:
    return [array1.Value(i) for i in range(1, array1.Length() + 1)]


def array1_to_int_list(array1: TColStd_Array1OfInteger) -> list[int]:
    return [array1.Value(i) for i in range(1, array1.Length() + 1)]


def array2_to_point_list(array2: TColgp_Array2OfPnt) -> list[list[Point]]:
    poles_list = []
    for i in range(1, array2.RowLength() + 1):
        row = []
        for j in range(1, array2.ColLength() + 1):
            pnt: gp_Pnt = array2.Value(i, j)
            row.append(Point(pnt.X(), pnt.Y(), pnt.Z()))
        poles_list.append(row)
    return poles_list

from OCC.Core.gp import gp_Pnt
from OCC.Core.TColgp import TColgp_Array1OfPnt, TColgp_Array2OfPnt
from OCC.Core.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal

from ada import Point


def array1_to_list(array1: TColStd_Array1OfReal) -> list[float]:
    return [array1.Value(i) for i in range(1, array1.Length() + 1)]


def array1_to_int_list(array1: TColStd_Array1OfInteger) -> list[int]:
    return [array1.Value(i) for i in range(1, array1.Length() + 1)]


def array2_to_point_list(array2: TColgp_Array2OfPnt) -> list[list[Point]]:
    """Convert OCC TColgp_Array2OfPnt to a 2D list of ada Point objects.

    Uses the array's actual lower/upper row/col bounds to avoid out-of-range errors.
    """
    poles_list: list[list[Point]] = []
    lr, ur = array2.LowerRow(), array2.UpperRow()
    lc, uc = array2.LowerCol(), array2.UpperCol()
    for i in range(lr, ur + 1):
        row: list[Point] = []
        for j in range(lc, uc + 1):
            pnt: gp_Pnt = array2.Value(i, j)
            row.append(Point(pnt.X(), pnt.Y(), pnt.Z()))
        poles_list.append(row)
    return poles_list


def array1_to_point_list(array1: TColgp_Array1OfPnt) -> list[Point]:
    """Convert OCC TColgp_Array1OfPnt to a list of ada Point objects."""
    pts: list[Point] = []
    for i in range(1, array1.Length() + 1):
        pnt: gp_Pnt = array1.Value(i)
        pts.append(Point(pnt.X(), pnt.Y(), pnt.Z()))
    return pts

from enum import Enum

import numpy as np

from ada.config import logger
from ada.fem.formats.code_aster.elem_shapes import ada_to_med_format
from ada.fem.shapes.definitions import BaseShapeEnum, LineShapes, MassTypes

#: A MED cell type names a geometry only, so several adapy types share one -- SE2 is a line, a
#: two-node spring and a connector alike -- and inverting ``ada_to_med_format`` let whichever was
#: listed last win. Every segment read back as a CONNECTOR, which a result bake then dropped, so a
#: Code_Aster beam model showed its nodes and none of its elements. Where the geometry is shared, read
#: the plain geometric shape.
_MED_TO_ADA_PREFERRED = {"SE2": LineShapes.LINE, "PO1": MassTypes.MASS}


def med_cell_values(co: np.ndarray, n_cells: int, n_points: int) -> np.ndarray:
    """A MED cell field's ``CO`` values as ``(n_cells, n_points, n_components)``.

    MED stores a field without interlacing (``MED_NO_INTERLACE``): component by component, and within one
    component every point of the first cell, then of the next -- for ``ELNO`` a cell's nodes, for ``ELGA`` its
    integration points. Read the other way round (``reshape((n_cells, n_points, -1), order="F")``, as meshio does)
    a field with more than one point per cell comes back with its values dealt across the cells: measured on a
    Code_Aster 18.1 beam model, the axial force of ``EFGE_ELNO`` was equal at a beam's two ends for 77 % of the
    beams read this way and for none read the other.
    """
    co = np.asarray(co)
    return co.reshape((-1, n_cells, n_points)).transpose(1, 2, 0)


def ada_to_med_type(ada_elem_type: BaseShapeEnum):
    """The MED cell type: geometry only. Reduced integration is chosen by the modelisation (3D_SI)."""
    result = ada_to_med_format.get(ada_elem_type, None)
    if result is None:
        raise KeyError(f'Unsupported value "{ada_elem_type}"')
    return result


def med_to_ada_type(value):
    if value in _MED_TO_ADA_PREFERRED:
        return _MED_TO_ADA_PREFERRED[value]

    _tmp = {v: k for k, v in ada_to_med_format.items()}

    if value not in _tmp:
        raise KeyError(f'Unsupported value "{value}"')

    res = _tmp[value]
    if type(res) is tuple:
        logger.info(f'Choosing index=0 -> "{res[0]}" when converting from MED type "{value}" to abaqus')
        return res[0]
    else:
        return res


class IntType(str, Enum):
    """Integer type for the mesh"""

    INT32 = "INT32"
    INT64 = "INT64"

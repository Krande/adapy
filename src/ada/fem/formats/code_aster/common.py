from enum import Enum

from ada.config import logger
from ada.fem.formats.code_aster.elem_shapes import ada_to_med_format
from ada.fem.shapes.definitions import BaseShapeEnum, LineShapes, MassTypes

#: A MED cell type names a geometry only, so several adapy types share one -- SE2 is a line, a
#: two-node spring and a connector alike -- and inverting ``ada_to_med_format`` let whichever was
#: listed last win. Every segment read back as a CONNECTOR, which a result bake then dropped, so a
#: Code_Aster beam model showed its nodes and none of its elements. Where the geometry is shared, read
#: the plain geometric shape.
_MED_TO_ADA_PREFERRED = {"SE2": LineShapes.LINE, "PO1": MassTypes.MASS}


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

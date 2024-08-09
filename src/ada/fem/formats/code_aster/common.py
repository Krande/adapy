from enum import Enum

from ada.config import logger
from ada.fem.exceptions import IncompatibleElements
from ada.fem.formats.code_aster.elem_shapes import ada_to_med_format
from ada.fem.shapes.definitions import BaseShapeEnum


def ada_to_med_type(ada_elem_type: BaseShapeEnum, reduced_integration: bool = False):
    result = ada_to_med_format.get(ada_elem_type, None)
    if result is None:
        raise KeyError(f'Unsupported value "{ada_elem_type}"')

    if reduced_integration is True:
        raise IncompatibleElements(f"Reduced integration is not yet supported for element type {ada_elem_type}")
        # reduced_elem = med_reduced_map.get(result, None)
        # if reduced_elem is None:
        #     logger.warning(f"Reduced integration is not supported for element type {result}")
        # else:
        #     result = reduced_elem

    return result


def med_to_ada_type(value):
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

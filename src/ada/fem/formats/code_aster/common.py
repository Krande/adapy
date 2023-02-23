from ada.config import get_logger
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes

logger = get_logger()


def ada_to_med_type(value):
    if value in _ada_to_med_type.keys():
        return _ada_to_med_type[value]
    else:
        for key, val in _ada_to_med_type.items():
            if type(key) is tuple:
                if value in key:
                    return val
    raise KeyError(f'Unsupported value "{value}"')


def med_to_ada_type(value):
    _tmp = {v: k for k, v in _ada_to_med_type.items()}

    if value not in _tmp:
        raise KeyError(f'Unsupported value "{value}"')

    res = _tmp[value]
    if type(res) is tuple:
        logger.info(f'Choosing index=0 -> "{res[0]}" when converting from MED type "{value}" to abaqus')
        return res[0]
    else:
        return res


_ada_to_med_type = {
    LineShapes.LINE: "SE2",
    LineShapes.LINE3: "SE3",
    ShellShapes.TRI: "TR3",
    ShellShapes.TRI6: "TR6",
    ShellShapes.TRI7: "TR7",  # Code Aster Specific type
    ShellShapes.QUAD: "QU4",
    ShellShapes.QUAD8: "QU8",
    ShellShapes.QUAD9: "QU9",  # Code Aster Specific type
    SolidShapes.TETRA: "TE4",
    SolidShapes.TETRA10: "T10",
    SolidShapes.HEX8: "HE8",
    SolidShapes.HEX20: "H20",
    SolidShapes.PYRAMID5: "PY5",
    # "pyramid13": "P13",
    SolidShapes.WEDGE: "PE6",
    # "wedge15": "P15",
}

import logging

from ada.fem import Step


def abaqus_to_med_type(value):
    if value in _abaqus_to_med_type.keys():
        return _abaqus_to_med_type[value]
    else:
        for key, val in _abaqus_to_med_type.items():
            if type(key) is tuple:
                if value in key:
                    return val
    raise KeyError(f'Unsupported value "{value}"')


def med_to_abaqus_type(value):
    _tmp = {v: k for k, v in _abaqus_to_med_type.items()}

    if value not in _tmp:
        raise KeyError(f'Unsupported value "{value}"')

    res = _tmp[value]
    if type(res) is tuple:
        logging.info(f'Choosing index=0 -> "{res[0]}" when converting from MED type "{value}" to abaqus')
        return res[0]
    else:
        return res


_abaqus_to_med_type = {
    "B31": "SE2",
    "B32": "SE3",
    "S3": "TR3",
    "STRI65": "TR6",
    ("S4", "S4R"): "QU4",
    ("S8R", "S8"): "QU8",
    "C3D4": "TE4",
    "C3D10": "T10",
    "C3D8": "HE8",
    ("C3D20R", "C3D20RH"): "H20",
    "C3D5": "PY5",
    # "pyramid13": "P13",
    "C3D6": "PE6",
    # "wedge15": "P15",
}


class CAStep(Step):
    def __init__(self, **kwargs):
        super(CAStep, self).__init__(**kwargs)

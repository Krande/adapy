import numpy as np


def write_ff(flag: str, data):
    """
    flag = NCOD
    data = [(int, float, int, float), (float, int)]

    ->> NCOD    INT     FLOAT       INT     FLOAT
                FLOAT   INT

    :param flag:
    :param data:
    :return:
    """

    out_str = f"{flag:<8}"
    for row in data:
        v = [format_data(x) for x in row]
        if row == data[-1]:
            out_str += "".join(v) + "\n"
        else:
            out_str += "".join(v) + "\n" + 8 * " "
    return out_str


def format_data(d):
    if type(d) in (np.float64, float, int, np.uint64, np.int32) and d >= 0:
        d = make_zero(d)
        return f"  {d:<14.8E}"
    elif type(d) in (np.float64, float, int, np.uint64, np.int32) and d < 0:
        d = make_zero(d)
        return f" {d:<15.8E}"
    elif isinstance(d, str):
        return d
    else:
        raise ValueError(f"Unknown input {type(d)} {d}")


def make_zero(d):
    return d if abs(d) != 0.0 else 0.0

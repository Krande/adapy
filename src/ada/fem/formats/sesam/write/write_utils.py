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
    # ``isinstance`` against the numpy ABCs (``np.integer`` /
    # ``np.floating``) covers every int / float numpy dtype the
    # downstream readers might produce — int32 / int64 / uint64 /
    # float32 / float64 — without needing to enumerate. Plain
    # Python ``int`` / ``float`` are covered by the
    # ``numbers``-shaped ``Real`` ABCs that ``isinstance`` honours,
    # but we test them explicitly for clarity since they're the
    # common case.
    if isinstance(d, (float, int, np.integer, np.floating)) and not isinstance(d, bool):
        d = make_zero(d)
        if d >= 0:
            return f"  {d:<14.8E}"
        return f" {d:<15.8E}"
    elif isinstance(d, str):
        return d
    else:
        raise ValueError(f"Unknown input {type(d)} {d}")


def make_zero(d):
    return d if abs(d) != 0.0 else 0.0

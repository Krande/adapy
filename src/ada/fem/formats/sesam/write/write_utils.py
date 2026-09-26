import contextlib
import contextvars

import numpy as np

#: Significant digits a Sesam number keeps: every field is E16.8 (a digit, 8 decimals).
SIGNIFICANT_DIGITS = 9


class Rounding:
    """How much E16.8 took off the numbers written while it was active: a count, and the
    worst relative change."""

    def __init__(self):
        self.count = 0
        self.max_rel = 0.0

    def record(self, value: float, written: float) -> None:
        self.count += 1
        rel = abs(written - value) / abs(value)
        if rel > self.max_rel:
            self.max_rel = rel


_rounding: contextvars.ContextVar[Rounding | None] = contextvars.ContextVar("sesam_rounding", default=None)


@contextlib.contextmanager
def track_rounding():
    """Count the numbers :func:`format_data` rounds inside the block."""
    tracker = Rounding()
    token = _rounding.set(tracker)
    try:
        yield tracker
    finally:
        _rounding.reset(token)


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

    # Join by position, not by value: comparing each row against ``data[-1]`` ended a
    # record early whenever an intermediate row happened to equal the last one.
    last = len(data) - 1
    parts = [f"{flag:<8}"]
    for i, row in enumerate(data):
        parts.append("".join([format_data(x) for x in row]))
        parts.append("\n" if i == last else "\n        ")
    return "".join(parts)


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
        text = f"  {d:<14.8E}" if d >= 0 else f" {d:<15.8E}"
        tracker = _rounding.get()
        if tracker is not None and float(text) != d:
            tracker.record(float(d), float(text))
        return text
    elif isinstance(d, str):
        return d
    else:
        raise ValueError(f"Unknown input {type(d)} {d}")


def make_zero(d):
    return d if abs(d) != 0.0 else 0.0

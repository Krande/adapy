# coding=utf-8
from __future__ import annotations

import hashlib
import pathlib
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

import numpy as np

from ada.config import Config, logger

if TYPE_CHECKING:
    from ada import Node


class NewLine:
    def __init__(self, n, prefix=None, suffix=None):
        self.i = 0
        self.n = n
        self.prefix = prefix
        self.suffix = suffix

    def __iter__(self):
        return self

    def __next__(self):
        if self.i < self.n:
            self.i += 1
            return ""
        else:
            self.i = 0
            prefix = self.prefix if self.prefix is not None else ""
            suffix = self.suffix if self.suffix is not None else ""
            return prefix + "\n" + suffix


class Counter:
    def __init__(self, start: int = 1, prefix: str = None):
        self.i = start - 1
        self._prefix = prefix

    def set_i(self, i):
        self.i = i

    @property
    def prefix(self):
        return self._prefix

    @prefix.setter
    def prefix(self, value):
        self._prefix = value

    def __iter__(self):
        return self

    def __next__(self):
        self.i += 1
        return self.i if self._prefix is None else f"{self._prefix}{self.i}"


def roundoff(x: float, precision=Config().general_precision) -> float:
    """Round using a specific number precision using the Decimal package"""
    xout = float(Decimal(float(x)).quantize(Decimal("." + precision * "0" + "1"), rounding=ROUND_HALF_EVEN))
    return xout if abs(xout) != 0.0 else 0.0


def get_current_user():
    """Return the username of currently logged in user"""
    import getpass

    return getpass.getuser()


def thread_this(list_in, function, cpus=4):
    """
    Make a function (which only takes in a list) to run on multiple processors

    :param list_in:
    :param function:
    :param cpus:
    :return:
    """
    import multiprocessing
    from functools import partial

    var = int(len(list_in) / cpus)
    blocks = [list_in[:var]]
    for i in range(1, cpus - 1):
        blocks.append(list_in[var * i : (i + 1) * var])

    blocks.append(list_in[(cpus - 1) * var :])
    pool = multiprocessing.Pool()
    func = partial(function)
    res = pool.map(func, blocks)
    pool.close()
    pool.join()
    # Join results from the various processes
    out_res = []
    for r in res:
        out_res += r
        print(r)
    return out_res


def bool2text(in_str):
    return "YES" if in_str is True else "NO"


def make_name_fem_ready(value, no_dot=False):
    """
    Based on typically allowed names in FEM, this function will try to rename objects to comply without significant
    changes to the original name

    :param value:
    :param no_dot:
    :return: Fixed name
    """
    logger.debug("Converting bad name")

    if value[0] == "/":
        value = value[1:]

    value = value.replace("/", "_").replace("=", "")
    if str.isnumeric(value[0]):
        value = "_" + value

    if "/" in value:
        logger.error(f'Character "/" found in {value}')

    # if "-" in value:
    #     value = value.replace("-", "_")

    if no_dot:
        value = value.replace(".", "_")
    final_name = value.strip()
    if len(final_name) > 25:
        logger.info(f'Note FEM name "{final_name}" is >25 characters. This might cause issues in some FEM software')
    return final_name


def get_version():
    from importlib.metadata import version

    return version("ada-py")


def flatten(t):
    return [item for sublist in t for item in sublist]


def set_list_first_position_elem(array: list, element) -> list:
    """Moves the element to the first position in the list and maintains order. Returns a new list."""
    origin_index = array.index(element)

    # shift the list so that the origin is the first point
    new_array = array[origin_index:] + array[:origin_index]
    return new_array


def get_md5_hash_for_file(filepath: str | pathlib.Path) -> hashlib._Hash:
    with open(filepath, "rb") as f:
        file_hash = hashlib.md5()
        while chunk := f.read(8192):
            file_hash.update(chunk)
        return file_hash


def to_real(v) -> float | list[float]:
    from ada import Node, Point

    if isinstance(v, float):
        return v
    elif isinstance(v, tuple):
        return [float(x) for x in v]
    elif isinstance(v, list):
        if isinstance(v[0], float):
            return v
        else:
            return [float(x) for x in v]
    elif isinstance(v, Node):
        return v.p.astype(float).tolist()
    elif isinstance(v, Point):
        return v.astype(float).tolist()
    else:
        return v.astype(float).tolist()


def round_array(arr: np.ndarray) -> np.ndarray:
    # roundoff only on nonzero entries, zeros stay exact
    mask = arr != 0
    out = arr.copy()
    out[mask] = np.vectorize(roundoff)(arr[mask])
    return out

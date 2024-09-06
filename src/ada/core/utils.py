# coding=utf-8
from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import shutil
import zipfile
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Any, Dict, Union

import numpy as np

from ada.config import Config, logger

if TYPE_CHECKING:
    from ada import Node


def is_package_installed(package_name):
    try:
        importlib.util.find_spec(package_name)
        return True
    except ImportError:
        return False


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
    import warnings

    warnings.filterwarnings(action="error", category=np.ComplexWarning)
    xout = float(Decimal(float(x)).quantize(Decimal("." + precision * "0" + "1"), rounding=ROUND_HALF_EVEN))
    return xout if abs(xout) != 0.0 else 0.0


def in_ipynb():
    try:
        from IPython import get_ipython

        get_ipython()
        return True
    except NameError:
        return False


def tuple_minus(t):
    return tuple([-roundoff(x) if x != 0.0 else 0.0 for x in t])


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


def download_to(destination, url, file_override_ok=False):
    """

    :param destination: Destination file path
    :param url: Url of file subject for download
    :param file_override_ok: Download and write over existing file
    """
    import urllib.request

    destination = pathlib.Path(destination)
    os.makedirs(destination.parent, exist_ok=True)

    if destination.exists() and file_override_ok is False:
        print("The destination file already exists. Will skip download again")
        return

    if destination.exists() is False:
        with urllib.request.urlopen(url) as response, open(destination, "wb") as out_file:
            shutil.copyfileobj(response, out_file)


def bool2text(in_str):
    return "YES" if in_str is True else "NO"


def traverse_hdf_datasets(hdf_file):
    """Traverse all datasets across all groups in HDF5 file."""

    import h5py

    def h5py_dataset_iterator(g, prefix=""):
        for key in g.keys():
            item = g[key]
            path = "{}/{}".format(prefix, key)
            if isinstance(item, h5py.Dataset):  # test for dataset
                yield (path, item)
            elif isinstance(item, h5py.Group):  # test for group (go down)
                yield from h5py_dataset_iterator(item, path)

    with h5py.File(hdf_file, "r") as f:
        for path, dset in h5py_dataset_iterator(f):
            print(path, dset)

    return None


def zip_it(filepath):
    import pathlib
    import zipfile

    fp = pathlib.Path(filepath)
    with zipfile.ZipFile(fp.with_suffix(".zip"), "w") as zip_archive:
        zip_archive.write(fp, arcname=fp.name, compress_type=zipfile.ZIP_DEFLATED)


def zip_dir(directory, zip_path, incl_only=None):
    """

    :param directory: Directory path subject for zipping
    :param zip_path: Destination path of zip file
    :param incl_only: (optional) List of suffixes that all files should have in order to be included in the zip file
    :return:
    """

    directory = pathlib.Path(directory)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_archive:
        for root, dirs, files in os.walk(directory):
            for file in files:
                if incl_only is not None:
                    keep = False
                    suffix = file.split(".")[-1]
                    if "." + suffix in incl_only:
                        keep = True
                    if keep is False:
                        continue
                zip_archive.write(
                    os.path.join(root, file),
                    os.path.relpath(os.path.join(root, file), os.path.join(directory, "..")),
                    # compress_type=zipfile.ZIP_DEFLATED
                )


def is_within_directory(directory, target):
    abs_directory = os.path.abspath(directory)
    abs_target = os.path.abspath(target)

    prefix = os.path.commonprefix([abs_directory, abs_target])

    return prefix == abs_directory


def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
    for member in tar.getmembers():
        member_path = os.path.join(path, member.name)
        if not is_within_directory(path, member_path):
            raise Exception("Attempted Path Traversal in Tar File")

    tar.extractall(path, members, numeric_owner)


def unzip_it(zip_path, extract_path=None):
    fp = pathlib.Path(zip_path)
    if extract_path is None:
        extract_path = fp.parents[0]
    if fp.suffix == ".gz":
        import tarfile

        with tarfile.open(fp) as tar:
            safe_extract(tar, extract_path)
    else:
        with zipfile.ZipFile(fp, "r") as zip_archive:
            zip_archive.extractall(extract_path)


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


def closest_val_in_dict(val: Union[int, float], dct: Dict[Union[int, float], Any]) -> Any:
    """
    When mapping using a dictionary and value do not match with the keys in the dictionary.
    :param val: Value a number, usually float
    :param dct: Dictionary with number keys (int o float)
    :return: Dictionary-value corresponding to the keys nearest the input value
    """
    table_looksups = np.array(list(dct))
    dct_index = table_looksups[np.abs(table_looksups - val).argmin()]
    return dct[dct_index]


def flatten(t):
    return [item for sublist in t for item in sublist]


def replace_node(old_node: Node, new_node: Node) -> None:
    from ada.fem import FemSet

    for elem in old_node.refs.copy():
        if isinstance(elem, FemSet):
            logger.warning("replace_node does not support updating FemSet")
            continue

        node_index = elem.nodes.index(old_node)

        elem.nodes.pop(node_index)
        elem.nodes.insert(node_index, new_node)
        elem.update()
        # new_node.refs.extend(old_node.refs)
        old_node.refs.pop(old_node.refs.index(elem))
        new_node.refs.append(elem)
        logger.debug(f"{old_node} exchanged with {new_node} --> {elem}")


def replace_nodes_by_tol(nodes, decimals=0, tol=Config().general_point_tol):
    """

    :param nodes:
    :param decimals:
    :param tol:
    :type nodes: ada.core.containers.Nodes
    """

    def rounding(vec, decimals_):
        return np.around(vec, decimals=decimals_)

    def n_is_most_precise(n, nearby_nodes_, decimals_=0):
        most_precise = [np.array_equal(n.p, rounding(n.p, decimals_)) for n in [node] + nearby_nodes_]

        if most_precise[0] and not np.all(most_precise[1:]):
            return True
        elif not most_precise[0] and np.any(most_precise[1:]):
            return False
        elif decimals_ == 10:
            logger.error(f"Recursion started at 0 decimals, but are now at {decimals_} decimals. Will proceed with n.")
            return True
        else:
            return n_is_most_precise(n, nearby_nodes_, decimals_ + 1)

    for node in nodes:
        nearby_nodes = list(filter(lambda x: x != node, nodes.get_by_volume(node.p, tol=tol)))
        if nearby_nodes and n_is_most_precise(node, nearby_nodes, decimals):
            for nearby_node in nearby_nodes:
                replace_node(nearby_node, node)


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

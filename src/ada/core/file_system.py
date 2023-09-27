import os
import pathlib
import shutil
import time
from typing import List, Union

from ada.config import logger


class SIZE_UNIT:
    """Enum for size units"""

    BYTES = 1
    KB = 2
    MB = 3
    GB = 4


def copy_bulk(files, destination_dir, substitution_map=None):
    """
    Use shutil to copy a list of files to a specified destination directory. Can also parse in a substitution map (a
    dict with key: value substitution for specified files

    :param files:
    :param destination_dir:
    :param substitution_map:
    :return:
    """
    if os.path.isdir(destination_dir):
        shutil.rmtree(destination_dir)
        time.sleep(1)
    os.makedirs(destination_dir, exist_ok=True)

    for f in files:
        fname = os.path.basename(f)
        dest_file = os.path.join(destination_dir, fname)
        edited = False
        if substitution_map is not None:
            if fname in substitution_map.keys():
                edited = True
                with open(f, "r") as d:
                    in_str = d.read()
                in_str = in_str.replace(substitution_map[fname][0], substitution_map[fname][1])
                with open(dest_file, "w") as d:
                    d.write(in_str)
        if edited is False:
            shutil.copy(f, dest_file)


def convert_unit(size_in_bytes, unit):
    """Convert the size from bytes to other units like KB, MB or GB"""
    if unit == SIZE_UNIT.KB:
        return size_in_bytes / 1024
    elif unit == SIZE_UNIT.MB:
        return size_in_bytes / (1024**2)
    elif unit == SIZE_UNIT.GB:
        return size_in_bytes / (1024**3)
    else:
        return size_in_bytes


def get_dir_size(root_directory: pathlib.Path):
    return sum(f.stat().st_size for f in root_directory.glob("**/*") if f.is_file())


def get_file_size(file_name, size_type=SIZE_UNIT.MB):
    """Get file in size in given unit like KB, MB or GB"""
    size = os.path.getsize(file_name)
    return convert_unit(size, size_type)


def get_short_path_name(long_name):
    """Gets the short path name of a given long path (https://stackoverflow.com/a/23598461/200291)"""
    import ctypes
    from ctypes import wintypes

    _GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
    _GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _GetShortPathNameW.restype = wintypes.DWORD

    output_buf_size = 0
    while True:
        output_buf = ctypes.create_unicode_buffer(output_buf_size)
        needed = _GetShortPathNameW(long_name, output_buf, output_buf_size)
        if output_buf_size >= needed:
            return output_buf.value
        else:
            output_buf_size = needed


def get_unc_path(path_) -> str:
    """Will try to convert path string to UNC path"""
    import win32wnet

    if path_[0].lower() == "c":
        return path_
    else:
        try:
            out_path = win32wnet.WNetGetUniversalName(path_)
            return out_path
        except BaseException as e:
            logger.error(e)
            return path_


def get_list_of_files(
    dir_path,
    file_ext=None,
    strict=False,
    filter_path_contains: Union[None, List[str], str] = None,
    keep_path_contains: Union[None, List[str], str] = None,
) -> list[str]:
    """Get a list of files and sub directories for a given directory"""
    all_files = []
    list_of_file = sorted(os.listdir(dir_path), key=str.lower)

    # Iterate over all the entries
    for entry in list_of_file:
        # Create full path
        full_path = os.path.join(dir_path, entry).replace(os.sep, "/")
        # If entry is a directory then get the list of files in this directory
        if os.path.isdir(full_path):
            all_files += get_list_of_files(full_path, file_ext, strict, filter_path_contains)
        else:
            if filter_path_contains is not None:
                if isinstance(filter_path_contains, str):
                    filter_path_contains = [filter_path_contains]
                skip_it = False
                for f in filter_path_contains:
                    if f in full_path:
                        skip_it = True
                        break
                if skip_it:
                    continue
            if keep_path_contains is not None:
                if isinstance(keep_path_contains, str):
                    keep_path_contains = [keep_path_contains]
                skip_it = True
                for f in keep_path_contains:
                    if f in full_path:
                        skip_it = False
                        break
                if skip_it:
                    continue
            all_files.append(full_path)

    if file_ext is not None:
        all_files = [f for f in all_files if f.endswith(file_ext)]

    if len(all_files) == 0:
        msg = f'Files with "{file_ext}"-extension is not found in "{dir_path}" or any sub-folder.'
        if strict:
            raise FileNotFoundError(msg)
        else:
            logger.info(msg)

    return all_files


def getfileprop(filepath: str) -> dict:
    """Read all properties of a local file and return them as a dictionary"""
    import win32api

    filepath = str(filepath)
    propNames = (
        "Comments",
        "InternalName",
        "ProductName",
        "CompanyName",
        "LegalCopyright",
        "ProductVersion",
        "FileDescription",
        "LegalTrademarks",
        "PrivateBuild",
        "FileVersion",
        "OriginalFilename",
        "SpecialBuild",
    )

    props = {"FixedFileInfo": None, "StringFileInfo": None, "FileVersion": None}

    try:
        # backslash as parm returns dictionary of numeric info corresponding to VS_FIXEDFILEINFO struc
        fixedInfo = win32api.GetFileVersionInfo(filepath, "\\")
        props["FixedFileInfo"] = fixedInfo
        props["FileVersion"] = "%d.%d.%d.%d" % (
            fixedInfo["FileVersionMS"] / 65536,
            fixedInfo["FileVersionMS"] % 65536,
            fixedInfo["FileVersionLS"] / 65536,
            fixedInfo["FileVersionLS"] % 65536,
        )

        # \VarFileInfo\Translation returns list of available (language, codepage)
        # pairs that can be used to retreive string info. We are using only the first pair.
        lang, codepage = win32api.GetFileVersionInfo(filepath, "\\VarFileInfo\\Translation")[0]

        # any other must be of the form \StringfileInfo\%04X%04X\parm_name, middle
        # two are language/codepage pair returned from above

        strInfo = {}
        for propName in propNames:
            strInfoPath = "\\StringFileInfo\\%04X%04X\\%s" % (lang, codepage, propName)
            strInfo[propName] = win32api.GetFileVersionInfo(filepath, strInfoPath)

        props["StringFileInfo"] = strInfo
    except Exception as e:
        logger.error(f'Unable to Read file properties due to "{e}"')
        pass

    return props


def path_leaf(path):
    import ntpath

    head, tail = ntpath.split(path)
    return tail or ntpath.basename(head)

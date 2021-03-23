import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import wraps

from ada.config import Settings as _Settings

try:
    # Python 3.6+
    from os import PathLike
except ImportError:
    from pathlib import PurePath as PathLike


class LocalExecute:
    """

    Backend Component for executing local analysis
    """

    def __init__(
        self,
        inp_path,
        cpus=2,
        gpus=None,
        run_ext=False,
        metadata=None,
        auto_execute=True,
        excute_locally=True,
    ):
        self._inp_path = pathlib.Path(inp_path)
        self._cpus = cpus
        self._gpus = gpus
        self._run_ext = run_ext
        self._metadata = metadata
        self._auto_execute = auto_execute
        self._local_execute = excute_locally

    def _run_local(self, run_command, stop_command=None, exit_on_complete=True):

        bat_start_str = f"""echo OFF
for %%* in (.) do set CurrDirName=%%~nx*
title %CurrDirName%
cd /d {self.analysis_dir}
echo ON
{run_command}"""
        if exit_on_complete is False:
            bat_start_str += "\npause"

        start_bat = "run.bat"
        stop_bat = "stop.bat"

        os.makedirs(self.execute_dir, exist_ok=True)
        with open(self.execute_dir / start_bat, "w") as d:
            d.write(bat_start_str + "\nEXIT")

        if stop_command is not None:
            with open(self.execute_dir / stop_bat, "w") as d:
                d.write(f"cd /d {self.analysis_dir}\n{stop_command}")

        if _Settings.execute_dir is not None:
            shutil.copy(self.execute_dir / start_bat, _Settings.execute_dir / start_bat)
            shutil.copy(self.execute_dir / stop_bat, _Settings.execute_dir / stop_bat)

        fem_tool = type(self).__name__

        print(80 * "-")
        print(f'starting {fem_tool} simulation "{self.analysis_name}"')
        if self._auto_execute is True:
            if self._run_ext is True:
                subprocess.run(
                    "start " + start_bat,
                    cwd=self.execute_dir,
                    shell=True,
                    env=os.environ,
                )
                print(f"Note! This starts {fem_tool} in an external window on a separate thread.")
            else:
                subprocess.run(
                    "start /wait " + start_bat,
                    cwd=self.execute_dir,
                    shell=True,
                    env=os.environ,
                )
                print(f'Finished {fem_tool} simulation "{self.analysis_name}"')
        print(80 * "-")

    def run(self):
        raise NotImplementedError("The run function is not implemented")

    @property
    def analysis_dir(self):
        return self.inp_path.parent

    @property
    def execute_dir(self):
        if _Settings.execute_dir is None:
            return self.analysis_dir
        else:
            return _Settings.execute_dir / self.analysis_name

    @property
    def analysis_name(self):
        return self.inp_path.stem

    @property
    def inp_path(self):
        return pathlib.Path(self._inp_path)


def is_buffer(obj, mode):
    return ("r" in mode and hasattr(obj, "read")) or ("w" in mode and hasattr(obj, "write"))


@contextmanager
def open_file(path_or_buf, mode="r"):
    if is_buffer(path_or_buf, mode):
        yield path_or_buf
    elif sys.version_info < (3, 6) and isinstance(path_or_buf, PathLike):
        # TODO remove when python 3.5 is EoL (i.e. 2020-09-13)
        # https://devguide.python.org/#status-of-python-branches
        # https://www.python.org/dev/peps/pep-0478/
        with open(str(path_or_buf), mode) as f:
            yield f
    else:
        with open(path_or_buf, mode) as f:
            yield f


def get_fem_model_from_assembly(assembly):
    """
    Scans the assembly tree for part (singular) containing FEM elements. Multiple parts with elements are not allowed

    :param assembly:
    :return: A single or multiple parts
    :rtype: ada.Part
    """
    parts = list(filter(lambda p: len(p.fem.elements) != 0, assembly.get_all_parts_in_assembly(True)))

    if len(parts) > 1:
        raise ValueError(
            "This method does not support multipart FEM. Please make sure your assembly only contain 1 FEM"
        )
    elif len(parts) == 0:
        raise ValueError("At least 1 part must have a FEM mesh ")

    return parts[0]


def get_exe_path(exe_name):
    """

    :param exe_name:
    :return:
    """
    import shutil

    from ada.config import Settings

    if Settings.fem_exe_paths[exe_name]:
        exe_path = Settings.fem_exe_paths[exe_name]
    elif os.getenv(f"ADA_{exe_name}_exe"):
        exe_path = os.getenv(f"ADA_{exe_name}_exe")
    elif shutil.which(f"{exe_name}.exe"):
        exe_path = shutil.which(f"{exe_name}.exe")
    elif shutil.which(f"{exe_name}.bat"):
        exe_path = shutil.which(f"{exe_name}.bat")
    else:
        raise FileNotFoundError(f'No Path to Executable "{exe_name}.exe" or "{exe_name}.bat" is found')

    exe_path = pathlib.Path(exe_path)

    if exe_path.exists() is False:
        raise FileNotFoundError(exe_path)

    return exe_path


def str_to_int(s):
    try:
        return int(float(s))
    except ValueError:
        raise ValueError("stop a minute")


def str_to_float(s):
    from ada.core.utils import roundoff

    return roundoff(s)


def get_ff_regex(flag, *args):
    """
    Compile a regex search string for Fortran formatted string input.

    :param flag: Name of keyword flag (ie. the first word on a line of input parameters)
    :param args: Group name for each parameter. Include the character | to signify the parameters is optional.


    :return: Returns a compiled regex search string.. re.compile(..)
    """
    pattern_str = r"^(?P<flag>.*?)" if flag is True else rf"^{flag}"
    counter = 0
    re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

    def add_key(k):
        if "|" in k:
            return rf'(?: \s*(?P<{k.replace("|", "")}>.*?)|)'
        return rf" \s*(?P<{k}>.*?)"

    for i, key in enumerate(args):
        if type(key) is str:
            pattern_str += add_key(key)
            counter += 1
            if counter == 4 and i < len(args) - 1:
                counter = 0
                pattern_str += r"(?:\n|)\s*"
        elif type(key) is list:
            for subkey in key:
                pattern_str += add_key(subkey)
            pattern_str += r"(?:\n|)\s*"
        else:
            raise ValueError(f'Unrecognized input type "{type(key)}"')

    if not args:
        pattern_str += add_key("bulk")

    return re.compile(pattern_str + r"(?:(?=^[A-Z]|\Z))", re_in)


def _overwrite_dir(analysis_dir):

    print("Removing old files before copying new")
    try:

        shutil.rmtree(analysis_dir)
    except WindowsError as e:
        print("Failed to delete due to '{}'".format(e))  # Or just pass

    time.sleep(0.5)
    os.makedirs(analysis_dir, exist_ok=True)


def _lock_check(analysis_dir):
    lck_file = (analysis_dir / analysis_dir.stem).with_suffix(".lck")
    if lck_file.is_file():
        raise IOError(
            f'Found lock-file:\n\n"{lck_file}"\n\nThis indicates that an analysis is running.'
            "Please stop analysis and try again"
        )
    if (analysis_dir / "ada.lck").is_file():
        raise IOError(
            f'Found ADA lock-file:\n\n"{analysis_dir}\\ada.lck"\n\nThis indicates that the analysis folder is'
            f" locked Please removed ADA lock file if this is not the case and try again"
        )


def _folder_prep(scratch_dir, analysis_name, overwrite):

    if scratch_dir is None:
        scratch_dir = pathlib.Path(_Settings.scratch_dir)
    else:
        scratch_dir = pathlib.Path(scratch_dir)

    analysis_dir = scratch_dir / analysis_name
    if analysis_dir.is_dir():
        _lock_check(analysis_dir)
        if overwrite is True:
            _overwrite_dir(analysis_dir)
        else:
            raise IOError("The analysis folder exists. Please remove folder and try again")

    os.makedirs(analysis_dir, exist_ok=True)
    return analysis_dir


def femio(f):
    """

    TODO: Make this into a file-identifcation utility and allow for storing cached FEM representations in a HDF5 file.

    :param f:
    :return:
    """

    def interpret_fem(fem_ref):
        fem_type = None
        if ".fem" in str(fem_ref).lower():
            fem_type = "sesam"
        elif ".inp" in str(fem_ref).lower():
            fem_type = "abaqus"
        return fem_type

    @wraps(f)
    def read_fem_wrapper(*args, **kwargs):
        from ada.fem.io import abaqus, calculix, code_aster, sesam, usfos

        from .io_meshio import meshio_read_fem, meshio_to_fem

        to_fem_map = dict(
            abaqus=abaqus.to_fem,
            sesam=sesam.to_fem,
            calculix=calculix.to_fem,
            usfos=usfos.to_fem,
            code_aster=code_aster.to_fem,
        )

        from_fem_map = dict(abaqus=abaqus.read_fem, sesam=sesam.read_fem, calculix=calculix.read_fem)
        f_name = f.__name__
        if f_name == "read_fem":
            fem_map = from_fem_map
        else:
            fem_map = to_fem_map

        fem_format = args[2] if len(args) >= 3 else kwargs.get("fem_format", None)
        fem_converter = kwargs.get("fem_converter", "default")
        if fem_format is None:
            fem_file = args[1]
            fem_format = interpret_fem(fem_file)

        if fem_format not in fem_map.keys() and fem_converter == "default":
            raise Exception(
                f"Currently not supporting import of fem type '{kwargs['fem_format']}' "
                "using the default fem converter. You could try to import using the 'meshio' fem_converter."
            )

        if fem_converter == "default":
            kwargs.pop("fem_converter", None)
            kwargs["convert_func"] = fem_map[fem_format]
        elif fem_converter.lower() == "meshio":
            if f_name == "read_fem":
                kwargs["convert_func"] = meshio_read_fem
            else:  # f_name == 'to_fem'
                if "metadata" not in kwargs.keys():
                    kwargs["metadata"] = dict()
                kwargs["metadata"]["fem_format"] = fem_format
                kwargs["convert_func"] = meshio_to_fem
        else:
            raise ValueError(f'Unrecognized fem_converter "{fem_converter}". Only "meshio" and "default" are supported')

        f(*args, **kwargs)

    return read_fem_wrapper

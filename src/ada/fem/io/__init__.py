import inspect
import os
import pathlib
import re
import shutil
import time
from functools import wraps

from ada.config import Settings as _Settings


class FemObjectReader:
    re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

    @staticmethod
    def str_to_int(s):
        try:
            return int(float(s))
        except ValueError:
            raise ValueError("stop a minute")

    @staticmethod
    def str_to_float(s):
        from ada.core.utils import roundoff

        return roundoff(s)

    @staticmethod
    def get_ff_regex(flag, *args):
        """
        Compile a regex search string for Fortran formatted string input.

        :param flag: Name of keyword flag (ie. the first word on a line of input parameters)
        :param args: Group name for each parameter. Include the character | to signify the parameters is optional.


        :return: Returns a compiled regex search string.. re.compile(..)
        """
        pattern_str = r"^(?P<flag>.*?)" if flag is True else rf"^{flag}"
        counter = 0

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

        return re.compile(pattern_str + r"(?:(?=^[A-Z]|\Z))", FemObjectReader.re_in)


class FemWriter:
    analysis_path = None

    def __init__(self):
        pass

    def _overwrite_dir(self):

        print("Removing old files before copying new")
        try:

            shutil.rmtree(self.analysis_path)
        except WindowsError as e:
            print("Failed to delete due to '{}'".format(e))  # Or just pass

        time.sleep(0.5)
        os.makedirs(self.analysis_path, exist_ok=True)

    def _write_dir(self, scratch_dir, analysis_name, overwrite):

        if scratch_dir is None:
            scratch_dir = pathlib.Path(_Settings.scratch_dir)
        else:
            scratch_dir = pathlib.Path(scratch_dir)

        self.analysis_path = scratch_dir / analysis_name
        if self.analysis_path.is_dir():
            self._lock_check()
            if overwrite is True:
                self._overwrite_dir()
            else:
                raise IOError("The analysis folder exists. Please remove folder and try again")

        os.makedirs(self.analysis_path, exist_ok=True)

    def _lock_check(self):
        lck_file = (self.analysis_path / self.analysis_path.stem).with_suffix(".lck")
        if lck_file.is_file():
            raise IOError(
                f'Found lock-file:\n\n"{lck_file}"\n\nThis indicates that an analysis is running.'
                "Please stop analysis and try again"
            )
        if (self.analysis_path / "ada.lck").is_file():
            raise IOError(
                f'Found ADA lock-file:\n\n"{self.analysis_path}\\ada.lck"\n\nThis indicates that the analysis folder is'
                f" locked Please removed ADA lock file if this is not the case and try again"
            )


class FemObjectInitializer:
    def __init__(self, fem_object, fem_writer):
        fem_origin = fem_object
        self._fem_writer = fem_writer

        pinit = inspect.getfullargspec(fem_origin.__init__)
        pargs = [x for x in pinit.args if x not in ("self", "units")]
        in_args = [name for name in fem_origin.__dict__.keys() if name[1:] in pargs]
        patts = {x[1:] if x[0] == "_" else x: fem_origin.__dict__[x] for x in in_args}
        if len(in_args) != len(pargs):
            raise Exception(
                f'''The class "{type(fem_origin)}" does not conform with the basic principle of the
Fem objects initialiser decorater. Please make sure that all passed arguments are identical to the declaration of the
variables to self with private prefix "_". Passed args: "{pargs}", Atts: "{fem_origin.__dict__.keys()}"'''
            )

        super().__init__(**patts)
        self.__dict__.update(fem_origin.__dict__)


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

    TODO: Make this a file-identifcation utility and allow for storing cached FEM representations in a HDF5 file.

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

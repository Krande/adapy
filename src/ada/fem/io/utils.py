import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from itertools import chain
from os import PathLike

from ada.concepts.containers import Beams, Plates
from ada.concepts.levels import Part
from ada.concepts.structural import Beam, Plate
from ada.config import Settings as _Settings


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
        self.run_ext = run_ext
        self._metadata = metadata
        self.auto_execute = auto_execute
        self.local_execute = excute_locally

    def _run_local(self, run_command, stop_command=None, exit_on_complete=True):

        if sys.platform == "linux" or sys.platform == "linux2":
            out = run_linux(self, run_command)
        elif sys.platform == "darwin":
            out = run_macOS(self, run_command)
        else:  # sys.platform == "win32":
            out = run_windows(self, run_command, stop_command, exit_on_complete)

        return out

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
            "This method does not yet support multipart FEM. Please make sure your assembly only contain 1 FEM"
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


def folder_prep(scratch_dir, analysis_name, overwrite):

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


def run_windows(exe: LocalExecute, run_command, stop_command=None, exit_on_complete=True):
    """

    :param exe:
    :param run_command:
    :param stop_command:
    :param exit_on_complete:
    :return:
    """
    bat_start_str = f"""echo OFF
for %%* in (.) do set CurrDirName=%%~nx*
title %CurrDirName%
cd /d {exe.analysis_dir}
echo ON\ncall {run_command}"""

    if exit_on_complete is False:
        bat_start_str += "\npause"

    start_bat = "run.bat"
    stop_bat = "stop.bat"

    os.makedirs(exe.execute_dir, exist_ok=True)

    with open(exe.execute_dir / start_bat, "w") as d:
        d.write(bat_start_str + "\nEXIT")

    if stop_command is not None:
        with open(exe.execute_dir / stop_bat, "w") as d:
            d.write(f"cd /d {exe.analysis_dir}\n{stop_command}")

    if _Settings.execute_dir is not None:
        shutil.copy(exe.execute_dir / start_bat, _Settings.execute_dir / start_bat)
        shutil.copy(exe.execute_dir / stop_bat, _Settings.execute_dir / stop_bat)

    fem_tool = type(exe).__name__

    print(80 * "-")
    print(f'starting {fem_tool} simulation "{exe.analysis_name}"')
    out = None
    if exe.auto_execute is True:
        if exe.run_ext is True:
            out = subprocess.run(
                "start " + start_bat,
                cwd=exe.execute_dir,
                shell=True,
                env=os.environ,
                capture_output=True,
                universal_newlines=True,
            )
            print(f"Note! This starts {fem_tool} in an external window on a separate thread.")
        else:
            out = subprocess.run(
                "start /wait " + start_bat,
                cwd=exe.execute_dir,
                shell=True,
                env=os.environ,
                capture_output=True,
                universal_newlines=True,
            )
            print(f'Finished {fem_tool} simulation "{exe.analysis_name}"')
    print(80 * "-")
    return out


def run_linux(exe, run_command):
    """

    :param exe:
    :param run_command:
    :return:
    """
    fem_tool = type(exe).__name__

    print(80 * "-")
    print(f'starting {fem_tool} simulation "{exe.analysis_name}" (on Linux)')
    if exe.auto_execute is True:
        if exe.run_ext is True:
            out = subprocess.run(
                run_command,
                cwd=exe.execute_dir,
                shell=True,
                env=os.environ,
                capture_output=True,
                universal_newlines=True,
            )
            print(f"Note! This starts {fem_tool} in an external window on a separate thread.")
        else:
            out = subprocess.run(
                run_command,
                cwd=exe.execute_dir,
                shell=True,
                env=os.environ,
                capture_output=True,
                universal_newlines=True,
            )
            print(f'Finished {fem_tool} simulation "{exe.analysis_name}"')
    print(80 * "-")
    return out


def run_macOS(exe, run_command):
    raise NotImplementedError()


def interpret_fem(fem_ref):
    fem_type = None
    if ".fem" in str(fem_ref).lower():
        fem_type = "sesam"
    elif ".inp" in str(fem_ref).lower():
        fem_type = "abaqus"
    return fem_type


def should_convert(res_path, overwrite):
    run_convert = True
    if res_path is not None:
        if res_path.exists() is True:
            run_convert = False
    if run_convert is True or overwrite is True:
        return True
    else:
        return False


def convert_shell_elem_to_plates(elem, parent) -> [Plate]:
    from ada.core.utils import is_coplanar

    plates = []
    fem_sec = elem.fem_sec
    fem_sec.material.parent = parent
    if len(elem.nodes) == 4:
        if is_coplanar(
            *elem.nodes[0].p,
            *elem.nodes[1].p,
            *elem.nodes[2].p,
            *elem.nodes[3].p,
        ):
            plates.append(Plate(f"sh{elem.id}", elem.nodes, fem_sec.thickness, use3dnodes=True, parent=parent))
        else:
            plates.append(Plate(f"sh{elem.id}", elem.nodes[:2], fem_sec.thickness, use3dnodes=True, parent=parent))
            plates.append(
                Plate(
                    f"sh{elem.id}_1",
                    [elem.nodes[0], elem.nodes[2], elem.nodes[3]],
                    fem_sec.thickness,
                    use3dnodes=True,
                    parent=parent,
                )
            )
    else:
        plates.append(Plate(f"sh{elem.id}", elem.nodes, fem_sec.thickness, use3dnodes=True, parent=parent))
    return plates


def convert_part_shell_elements_to_plates(p) -> Plates:

    return Plates(list(chain.from_iterable([convert_shell_elem_to_plates(sh, p) for sh in p.fem.elements.shell])))


def convert_part_elem_bm_to_beams(p) -> Beams:
    return Beams([elem_to_beam(bm, p) for bm in p.fem.elements.beams])


def elem_to_beam(elem, parent):
    """
    TODO: Evaluate merging elements by proximity and equal section and normals.

    :param elem:
    :param parent: Parent Part object
    :type elem: ada.fem.Elem
    """

    n1 = elem.nodes[0]
    n2 = elem.nodes[-1]
    offset = elem.fem_sec.offset
    e1 = None
    e2 = None
    elem.fem_sec.material.parent = parent
    if offset is not None:
        for no, ecc in offset:
            if no.id == n1.id:
                e1 = ecc
            if no.id == n2.id:
                e2 = ecc

    if elem.fem_sec.section.type == "GENBEAM":
        logging.error(f"Beam elem {elem.id}  uses a GENBEAM which might not represent an actual cross section")

    return Beam(
        f"bm{elem.id}",
        n1,
        n2,
        elem.fem_sec.section,
        elem.fem_sec.material,
        up=elem.fem_sec.local_z,
        e1=e1,
        e2=e2,
        parent=parent,
    )


def convert_part_objects(p: Part, skip_plates, skip_beams):
    if skip_plates is False:
        p._plates = convert_part_shell_elements_to_plates(p)
    if skip_beams is False:
        p._beams = convert_part_elem_bm_to_beams(p)

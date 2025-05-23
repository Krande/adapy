from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from itertools import chain
from typing import TYPE_CHECKING

from ada.api.containers import Beams, Plates
from ada.config import Config, logger
from ada.fem import Elem
from ada.fem.exceptions import FEASolverNotInstalled

if TYPE_CHECKING:
    from ada import Assembly, Beam, Part, Plate
    from ada.fem.formats.general import FEATypes


class DatFormatReader:
    re_flags = re.MULTILINE | re.DOTALL
    re_int = r"[0-9]{1,9}"
    re_decimal_float = r"[+|-]{0,1}[0-9]{1,9}\.[0-9]{0,6}"
    re_decimal_scientific = r"[+|-]{0,1}[0-9]{1,2}\.[0-9]{5,7}[E|e][\+|\-][0-9]{2}"

    def compile_ff_re(self, list_of_types, separator=None):
        """Create a compiled regex pattern for a specific combination of floats and ints provided"""
        re_str = r"^\s*("
        sep_str = "" if separator is None else separator
        for i, t in enumerate(list_of_types):
            if i == len(list_of_types) - 1:
                sep_str = ""
            if t is int:
                re_str += rf"{self.re_int}{sep_str}\s*"
            elif t is float:
                re_str += rf"(?:{self.re_decimal_scientific}|{self.re_decimal_float}){sep_str}\s*"
            else:
                raise ValueError()
        re_str += r")\n"
        return re.compile(re_str, self.re_flags)

    def read_data_lines(self, dat_file, regex: re.Pattern, start_flag, end_flag=None, split_data=False) -> list:
        """Reads line by line without any spaces to search for strings while disregarding formatting"""
        read_data = False
        results = []
        with open(dat_file, "r") as f:
            for line in f.readlines():
                compact_str = line.replace(" ", "").strip().lower()
                if start_flag in compact_str:
                    read_data = True
                if end_flag is not None and end_flag in compact_str:
                    return results
                if read_data is False:
                    continue
                res = regex.search(line)
                if res is not None:
                    result_data = res.group(1)
                    if split_data:
                        result_data = result_data.split()
                    results.append(result_data)

        return results


class LocalExecute:
    """Backend Component for executing local analysis"""

    def __init__(
        self,
        inp_path,
        cpus=2,
        gpus=None,
        run_ext=False,
        metadata=None,
        auto_execute=True,
        excute_locally=True,
        run_in_shell=False,
    ):
        self._inp_path = pathlib.Path(inp_path)
        self._cpus = cpus
        self._gpus = gpus
        self.run_ext = run_ext
        self._metadata = metadata
        self.auto_execute = auto_execute
        self.local_execute = excute_locally
        self.run_in_shell = run_in_shell

    def _run_local(self, run_command, stop_command=None, exit_on_complete=True, bat_start_str=None):
        if self._metadata is not None:
            with open(self.inp_path.parent / "analysis_manifest.json", "w") as fp:
                json.dump(self._metadata, fp, indent=4)

        if sys.platform == "linux" or sys.platform == "linux2":
            logger.info("Running on Linux platform.")
            out = run_linux(self, run_command)
        elif sys.platform == "darwin":
            logger.info("Running on macOS platform.")
            out = run_macOS(self, run_command)
        else:  # sys.platform == "win32":
            logger.info("Running on Windows platform.")
            out = run_windows(
                self,
                run_command,
                stop_command,
                exit_on_complete,
                bat_start_str=bat_start_str,
                run_in_shell=self.run_in_shell,
            )

        return out

    def run(self):
        raise NotImplementedError("The run function is not implemented")

    @property
    def analysis_dir(self):
        return self.inp_path.parent

    @property
    def execute_dir(self):
        if Config().fea_execute_dir is None:
            return self.analysis_dir
        else:
            return Config().fea_execute_dir / self.analysis_name

    @property
    def analysis_name(self):
        return self.inp_path.stem

    @property
    def inp_path(self):
        return pathlib.Path(self._inp_path)

    @inp_path.setter
    def inp_path(self, value):
        self._inp_path = pathlib.Path(value)

    @property
    def cpus(self):
        return self._cpus

    def get_exe(self, fea_software):
        from ada.fem.formats.general import fem_solver_map

        solver_exe_name = fem_solver_map.get(fea_software, fea_software)
        exe_path = None
        for exe_test in [fea_software, solver_exe_name]:
            try:
                exe_path = get_exe_path(exe_test)
            except FileNotFoundError:
                continue
            if exe_path is not None:
                break

        if exe_path is None:
            msg = (
                f'FEA Solver executable for "{solver_exe_name}" is not found. '
                f"Please make sure that an executable exists at the specified location.\n"
                f"See section about adding FEA solvers to paths "
                f"so that adapy finds them in the readme at https://github.com/Krande/adapy"
            )
            raise FEASolverNotInstalled(msg)

        return exe_path


def is_buffer(obj, mode):
    return ("r" in mode and hasattr(obj, "read")) or ("w" in mode and hasattr(obj, "write"))


@contextmanager
def open_file(path_or_buf, mode="r"):
    if is_buffer(path_or_buf, mode):
        yield path_or_buf
    else:
        with open(path_or_buf, mode) as f:
            yield f


def get_fem_model_from_assembly(assembly: Assembly) -> Part:
    """
    Scans the assembly tree for parts containing FEM elements. If multiple FEM objects are not empty,
    they will be merged
    """
    parts = list(filter(lambda p: p.fem.is_empty() is False, assembly.get_all_parts_in_assembly(True)))

    if len(parts) > 1:
        raise ValueError(
            "This method does not yet support multipart FEM. Please make sure your assembly only contain 1 FEM"
        )
    elif len(parts) == 0:
        raise ValueError("At least 1 part must have a FEM mesh ")

    return parts[0]


def get_exe_path(fea_type: FEATypes):
    from ada.fem.formats.general import FEATypes

    if isinstance(fea_type, FEATypes):
        exe_name = fea_type.value
    else:
        exe_name = fea_type

    env_name = f"ADA_{exe_name}_exe"
    env_path = os.getenv(env_name, None)
    if env_path is not None:
        exe_path = pathlib.Path(env_path)
        if exe_path.exists():
            return exe_path

    exe_linux = shutil.which(exe_name)
    bin_exe_linux = pathlib.Path(sys.prefix) / f"bin/{exe_name}"
    bin_exe_win = pathlib.Path(sys.prefix) / f"Library/bin/{exe_name}.exe"
    bat_bat_win = bin_exe_win.with_suffix(".bat")

    if exe_linux is None and bin_exe_linux.exists():
        exe_linux = bin_exe_linux
    exe_win = shutil.which(f"{exe_name}.exe")
    if exe_win is None and bin_exe_win.exists():
        exe_win = bin_exe_win
    if exe_win is None and bat_bat_win.exists():
        exe_win = bat_bat_win

    if Config().fea_fem_exe_paths.get(exe_name, None) is not None:
        exe_path = Config().fea_fem_exe_paths[exe_name]
    elif exe_linux:
        exe_path = exe_linux
    elif exe_win:
        exe_path = exe_win
    elif shutil.which(f"{exe_name}.bat"):
        exe_path = shutil.which(f"{exe_name}.bat")
    else:
        raise FileNotFoundError(f'No Path to Executable "{exe_name}.exe" or "{exe_name}.bat" is found')

    exe_path = pathlib.Path(exe_path)

    if exe_path.exists() is False:
        return None

    return exe_path


def str_to_int(s):
    return int(float(s))


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
        if isinstance(key, str):
            pattern_str += add_key(key)
            counter += 1
            if counter == 4 and i < len(args) - 1:
                counter = 0
                pattern_str += r"(?:\n|)\s*"
        elif isinstance(key, list):
            for subkey in key:
                pattern_str += add_key(subkey)
            pattern_str += r"(?:\n|)\s*"
        else:
            raise ValueError(f'Unrecognized input type "{type(key)}"')

    if not args:
        pattern_str += add_key("bulk")

    pattern_str += r"(?:(?=^[A-Z]|\Z))"

    return re.compile(pattern_str, re_in)


def _overwrite_dir(analysis_dir):
    from send2trash import send2trash

    logger.info("Removing old files before copying new")
    try:
        if Config().general_safe_deletion is True:
            send2trash(analysis_dir)
        else:
            shutil.rmtree(analysis_dir)
    except BaseException as e:
        logger.warning(f"Failed to delete due to '{e}'")

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
        scratch_dir = pathlib.Path(Config().fea_scratch_dir)
    else:
        scratch_dir = pathlib.Path(scratch_dir)

    analysis_dir = scratch_dir / analysis_name
    if analysis_dir.is_dir():
        _lock_check(analysis_dir)
        if overwrite is False:
            raise IOError('The analysis folder exists. Please remove folder or pass argument "overwrite=True"')

        _overwrite_dir(analysis_dir)

    os.makedirs(analysis_dir, exist_ok=True)
    return analysis_dir


def run_windows(exe: LocalExecute, run_cmd, stop_cmd=None, exit_after=True, bat_start_str=None, run_in_shell=False):
    if bat_start_str is None:
        bat_start_str = f"""echo OFF
for %%* in (.) do set CurrDirName=%%~nx*
title %CurrDirName%
cd /d {exe.analysis_dir}
echo ON\ncall {run_cmd}"""

    if exit_after is False:
        bat_start_str += "\npause"

    start_bat = "run.bat"
    stop_bat = "stop.bat"

    os.makedirs(exe.execute_dir, exist_ok=True)

    with open(exe.execute_dir / start_bat, "w") as d:
        d.write(bat_start_str + "\nEXIT")

    if stop_cmd is not None:
        with open(exe.execute_dir / stop_bat, "w") as d:
            d.write(f"cd /d {exe.analysis_dir}\n{stop_cmd}")

    if Config().fea_execute_dir is not None:
        shutil.copy(exe.execute_dir / start_bat, Config().fea_execute_dir / start_bat)
        shutil.copy(exe.execute_dir / stop_bat, Config().fea_execute_dir / stop_bat)

    # If the script should be running from batch files, then this can be used
    if run_in_shell:
        _ = "start " + start_bat if exe.run_ext is True else "start /wait " + start_bat
    else:
        _ = "start " + start_bat if exe.run_ext is True else "call " + start_bat

    return run_tool(exe, run_cmd, "Windows")


def run_linux(exe, run_cmd):
    return run_tool(exe, run_cmd.split(), "Linux")


def run_tool(exe: LocalExecute, run_cmd, platform):
    fem_tool_name = type(exe).__name__.replace("Execute", "")
    props = dict(cwd=exe.execute_dir, env=os.environ, universal_newlines=True, encoding="utf-8")
    if exe.auto_execute is False:
        return None

    print(80 * "-")
    print(f'Starting {fem_tool_name} simulation "{exe.analysis_name}" (on {platform}) using {exe.cpus} cpus')
    if exe.run_ext is True:
        out = subprocess.Popen(run_cmd, **props)
        print(f"Note! This starts {fem_tool_name} in an external window on a separate thread.")
    else:
        # run_directly_on_windows(run_cmd, props, exe)
        props["capture_output"] = True
        out = subprocess.run(run_cmd, **props)
        print(f'Finished {fem_tool_name} simulation "{exe.analysis_name}"')
    print(80 * "-")
    return out


def run_directly_on_windows(run_cmd, props, exe):
    rstr = ""
    for out in execute(cmd=run_cmd, **props):
        print(out)
        rstr += out
    with open(exe.execute_dir / "run.log", "w") as f:
        f.write(rstr)


def execute(cmd, cwd, encoding, **kwargs):
    popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, cwd=cwd, encoding=encoding)
    for stdout_line in popen.stdout:
        try:
            yield stdout_line.strip()
        except UnicodeDecodeError as e:
            logger.error(e)
            continue

    popen.stdout.close()
    return_code = popen.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, cmd)


def run_macOS(exe, run_cmd):
    raise NotImplementedError()


def interpret_fem_format_from_path(fem_path: str | pathlib.Path):
    from ada.fem.formats.general import FEATypes

    suffix = pathlib.Path(fem_path).suffix.lower()

    fem_type = None
    if suffix in (".fem", ".sif"):
        fem_type = FEATypes.SESAM
    elif suffix == ".inp":
        fem_type = FEATypes.ABAQUS
    elif suffix in (".frd",):
        fem_type = FEATypes.CALCULIX
    elif suffix in (".rmed", ".med"):
        fem_type = FEATypes.CODE_ASTER
    else:
        logger.error(f'unrecognized suffix "{suffix}"')

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


def convert_shell_elem_to_plates(elem: Elem, parent: Part) -> list[Plate]:
    from ada import Plate
    from ada.core.vector_utils import is_coplanar

    plates = []
    fem_sec = elem.fem_sec
    fem_sec.material.parent = parent
    mat_dict = {}

    new_mat = mat_dict.get(fem_sec.material.name, None)
    if new_mat is None:
        new_mat = parent.materials.add(fem_sec.material.copy_to(fem_sec.material.name, parent=parent))
        mat_dict[fem_sec.material.name] = new_mat

    if len(elem.nodes) == 4:
        if is_coplanar(
            *elem.nodes[0].p,
            *elem.nodes[1].p,
            *elem.nodes[2].p,
            *elem.nodes[3].p,
        ):
            plates.append(
                Plate.from_3d_points(
                    f"sh{elem.id}", [n.p for n in elem.nodes], fem_sec.thickness, mat=new_mat, parent=parent
                )
            )
        else:
            el_n1 = [elem.nodes[0].p, elem.nodes[1].p, elem.nodes[2].p]
            el_n2 = [elem.nodes[0].p, elem.nodes[2].p, elem.nodes[3].p]
            plates.append(Plate.from_3d_points(f"sh{elem.id}", el_n1, fem_sec.thickness, mat=new_mat, parent=parent))
            plates.append(
                Plate.from_3d_points(
                    f"sh{elem.id}_1",
                    el_n2,
                    fem_sec.thickness,
                    mat=new_mat,
                    parent=parent,
                )
            )
    else:
        try:
            plates.append(
                Plate.from_3d_points(
                    f"sh{elem.id}",
                    [n.p for n in elem.nodes],
                    fem_sec.thickness,
                    mat=fem_sec.material,
                    parent=parent,
                )
            )
        except BaseException as e:
            logger.error(f"Unable to convert {elem.id=} to plate due to {e}")

    return plates


def convert_part_shell_elements_to_plates(p: Part) -> Plates:
    return Plates(
        list(chain.from_iterable([convert_shell_elem_to_plates(sh, p) for sh in p.fem.elements.shell])), parent=p
    )


def convert_part_elem_bm_to_beams(p: Part) -> Beams:
    return Beams([line_elem_to_beam(bm, p) for bm in p.fem.elements.lines], parent=p)


def line_elem_to_beam(elem: Elem, parent: Part, prefix="bm") -> Beam:
    """Convert FEM line element to Beam"""
    from ada import Beam

    n1 = elem.nodes[0]
    n2 = elem.nodes[-1]
    e1 = None
    e2 = None
    elem.fem_sec.material.parent = parent
    if Config().fem_convert_options_fem2concepts_include_ecc is True:
        if elem.eccentricity is not None:
            ecc = elem.eccentricity
            if ecc.end1 is not None and ecc.end1.node.id == n1.id:
                e1 = ecc.end1.ecc_vector
            if ecc.end2 is not None and ecc.end2.node.id == n2.id:
                e2 = ecc.end2.ecc_vector

    if elem.fem_sec.section.type == "GENBEAM":
        logger.error(f"Beam elem {elem.id}  uses a GENBEAM which might not represent an actual cross section")

    return Beam(
        f"{prefix}{elem.id}",
        n1,
        n2,
        sec=elem.fem_sec.section,
        mat=elem.fem_sec.material,
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


def default_fem_res_path(
    name, scratch_dir=None, analysis_dir=None, fem_format=None
) -> dict[FEATypes, pathlib.Path] | str:
    from ada.fem.formats.general import FEATypes

    if scratch_dir is None and analysis_dir is None:
        scratch_dir = Config().fea_scratch_dir

    base_path = scratch_dir / name / name if analysis_dir is None else analysis_dir / name
    fem_format_map = {
        FEATypes.CODE_ASTER: base_path.with_suffix(".rmed"),
        FEATypes.ABAQUS: base_path.with_suffix(".odb"),
        FEATypes.CALCULIX: base_path.with_suffix(".frd"),
        FEATypes.SESAM: (base_path.parent / f"{name}R1").with_suffix(".SIN"),
        FEATypes.USFOS: base_path.with_suffix(".fem"),
        FEATypes.XDMF: base_path.with_suffix(".xdmf"),
    }

    if fem_format is None:
        return fem_format_map

    return fem_format_map.get(fem_format)


def default_fem_inp_path(name, scratch_dir=None, analysis_dir=None):
    from ada.fem.formats.general import FEATypes

    base_path = scratch_dir / name / name if analysis_dir is None else analysis_dir / name
    return {
        FEATypes.CODE_ASTER: base_path.with_suffix(".export"),
        FEATypes.ABAQUS: base_path.with_suffix(".inp"),
        FEATypes.CALCULIX: base_path.with_suffix(".inp"),
        FEATypes.SESAM: (base_path.parent / f"{name}T1").with_suffix(".FEM"),
        FEATypes.USFOS: base_path.with_suffix(".raf"),
    }

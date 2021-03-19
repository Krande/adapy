import os
import pathlib
import sys
from contextlib import contextmanager

try:
    # Python 3.6+
    from os import PathLike
except ImportError:
    from pathlib import PurePath as PathLike


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


class LocalExecute:
    """

    Backend Component for executing local analysis
    """

    alt_execute_dir = None
    _exe_path = None

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
        self._inp_path = inp_path
        self._cpus = cpus
        self._gpus = gpus
        self._run_ext = run_ext
        self._metadata = metadata
        self._auto_execute = auto_execute
        self._local_execute = excute_locally

    def _run_local(self, run_command, stop_command=None, exit_on_complete=True):
        import shutil
        import subprocess

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

        if self.alt_execute_dir is not None:
            shutil.copy(self.execute_dir / start_bat, self.analysis_dir / start_bat)

        if stop_command is not None:
            with open(self.execute_dir / stop_bat, "w") as d:
                d.write(f"cd /d {self.analysis_dir}\n{stop_command}")

            if self.alt_execute_dir is not None:
                shutil.copy(self.execute_dir / stop_bat, self.analysis_dir / stop_bat)

        fem_tool = type(self).__name__

        print(80 * "-")
        print(f'starting {fem_tool} simulation "{self.analysis_name}"')
        if self._auto_execute is True:
            if self._run_ext is True:
                subprocess.run("start " + start_bat, cwd=self.execute_dir, shell=True)
                print(f"Note! This starts {fem_tool} in an external window on a separate thread.")
            else:
                subprocess.run("start /wait " + start_bat, cwd=self.execute_dir, shell=True)
                print(f'Finished {fem_tool} simulation "{self.analysis_name}"')
        print(80 * "-")

    def run(self):
        raise NotImplementedError("The run function is not implemented")

    @property
    def analysis_dir(self):
        return self.inp_path.parent

    @property
    def execute_dir(self):
        if self.alt_execute_dir is None:
            return self.analysis_dir
        else:
            return self.alt_execute_dir / self.analysis_name

    @property
    def analysis_name(self):
        return self.inp_path.stem

    @property
    def inp_path(self):
        return pathlib.Path(self._inp_path)

    @property
    def exe_path(self):
        return pathlib.Path(self._exe_path)

    @property
    def exe_dir(self):
        return self.exe_path.parents[0]


def get_fem_exe_paths():
    from .calculix.execute import Calculix
    from .code_aster.execute import CodeAsterAnalysis

    return dict(ccx=Calculix.exe_path, code_aster=CodeAsterAnalysis.exe_path)


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

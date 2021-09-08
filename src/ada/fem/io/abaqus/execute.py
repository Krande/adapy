import logging
import pathlib
import shutil

from ada.fem.io.utils import LocalExecute, get_exe_path


def run_abaqus(
    inp_path,
    cpus=2,
    gpus=None,
    run_ext=False,
    metadata=None,
    subr_path=None,
    execute=True,
    return_bat_str=False,
    exit_on_complete=True,
    run_in_shell=False,
):
    """

    :param inp_path: Path to input file folder(s)
    :param cpus: Number of CPUs to run the analysis on. Default is 2.
    :param gpus: Number of GPUs to run the analysis on. Default is none.
    :param run_ext: If False the process will wait for the abaqus analysis to finish. Default is False
    :param metadata: Dictionary containing various metadata relevant for the analysis
    :param subr_path: Path to fortran subroutine file (optional).
    :param execute: Automatically starts Abaqus analysis. Default is True
    :param return_bat_str:
    :param exit_on_complete:
    :param run_in_shell:
    """

    gpus = "" if gpus is None else f"GPUS={gpus}"
    run_cmd = None
    custom_bat_str = None
    if subr_path is not None:
        run_cmd, custom_bat_str = create_subroutine_input(inp_path, subr_path, cpus, gpus)

    aba_exe = AbaqusExecute(
        inp_path, cpus=cpus, run_ext=run_ext, metadata=metadata, auto_execute=execute, run_in_shell=run_in_shell
    )
    return aba_exe.run(exit_on_complete, run_cmd=run_cmd, bat_start_str=custom_bat_str)


class AbaqusExecute(LocalExecute):
    def run(self, exit_on_complete=True, run_cmd=None, bat_start_str=None):
        try:
            exe_path = get_exe_path("abaqus")
        except FileNotFoundError as e:
            logging.error(e)
            return
        gpus = "" if self._gpus is None else f"GPUS={self._gpus}"
        if run_cmd is None:
            run_cmd = f"{exe_path} job={self.analysis_name} CPUS={self._cpus}{gpus} interactive"
        stop_cmd = f"abaqus terminate job={self.analysis_name}"
        out = self._run_local(run_cmd, stop_cmd, exit_on_complete, bat_start_str)
        return out


def create_subroutine_input(inp_path, subroutine_path, cpus, gpus):
    inp_path = pathlib.Path(inp_path)
    if inp_path.exists() is False:
        raise FileNotFoundError(f'Unable to find inp file "{inp_path}"')
    analysis_name = inp_path.name.replace(".inp", "")
    subroutine_path = pathlib.Path(subroutine_path)
    subr_name = subroutine_path.with_suffix("").name
    shutil.copy(subroutine_path, inp_path.parent / subroutine_path.name)
    subr = f"user={subr_name}"
    param = ["job=" + analysis_name, subr, "CPUS=" + str(cpus), gpus, "interactive"]
    run_cmd = " ".join([str(val) for val in param if str(val) != ""])
    prog = r"C:\Program Files (x86)"
    custom_bat_str = rf'''
call "{prog}\IntelSWTools\compilers_and_libraries_2018.3.210\windows\bin\ipsxe-comp-vars.bat" intel64 vs2017
call "{prog}\Microsoft Visual Studio 11.0\VC\bin\amd64\vcvars64.bat" intel 64
call "C:\SIMULIA\CAE\2017\win_b64\code\bin\ABQLauncher.exe"'''
    return run_cmd, custom_bat_str

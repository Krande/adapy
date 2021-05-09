import json
import logging
import os
import pathlib
import shutil
import subprocess

from ada.config import Settings as _Settings


def run_abaqus(
    inp_path,
    cpus=2,
    gpus=None,
    run_ext=False,
    manifest=None,
    subr_path=None,
    execute=True,
    return_bat_str=False,
    exit_on_complete=True,
):
    """

    :param inp_path: Path to input file folder(s)
    :param cpus: Number of CPUs to run the analysis on. Default is 2.
    :param gpus: Number of GPUs to run the analysis on. Default is none.
    :param run_ext: If False the process will wait for the abaqus analysis to finish. Default is False
    :param manifest: Dictionary containing various metadata relevant for the analysis
    :param subr_path: Path to fortran subroutine file (optional).
    :param execute: Automatically starts Abaqus analysis. Default is True
    :param return_bat_str:
    :param exit_on_complete:


    Note!
        'analysis_name' be name of input file if 'analysis_path' is pointing to containing folder.
        Alternatively if name of input file and containing folder shares the same name 'analysis_path' can be set to the
        top folder and analysis_name will refer to the subfolder and inp-file sharing the same name.

    """
    from ..utils import get_exe_path

    try:
        aba_dir = get_exe_path("abaqus")
    except FileNotFoundError as e:
        logging.error(e)
        return

    inp_path = pathlib.Path(inp_path)

    if inp_path.exists() is False:
        raise FileNotFoundError(f'Unable to find inp file "{inp_path}"')

    gpus = "" if gpus is None else f"GPUS={gpus}"
    analysis_name = inp_path.name.replace(".inp", "")

    if subr_path is None:
        param = ["job=" + analysis_name, "CPUS=" + str(cpus), gpus, "interactive"]
        call_str = f"call {aba_dir}"
    else:
        subr_path = pathlib.Path(subr_path)
        subr_name = subr_path.with_suffix("").name
        shutil.copy(subr_path, inp_path.parent / subr_path.name)
        subr = f"user={subr_name}"
        param = ["job=" + analysis_name, subr, "CPUS=" + str(cpus), gpus, "interactive"]
        prog = r"C:\Program Files (x86)"
        call_str = rf'''
call "{prog}\IntelSWTools\compilers_and_libraries_2018.3.210\windows\bin\ipsxe-comp-vars.bat" intel64 vs2017
call "{prog}\Microsoft Visual Studio 11.0\VC\bin\amd64\vcvars64.bat" intel 64
call "C:\SIMULIA\CAE\2017\win_b64\code\bin\ABQLauncher.exe"'''
    param_str = " ".join([str(val) for val in param if str(val) != ""])

    start_bat = "runABA.bat"
    stop_bat = "stopABA.bat"

    bat_start_str = f"""echo OFF
for %%* in (.) do set CurrDirName=%%~nx*
title %CurrDirName%
cd /d {inp_path.parent}
echo ON
{call_str} {param_str}"""

    if exit_on_complete:
        bat_start_str += "\nEXIT\nEXIT"

    with open(inp_path.parent / start_bat, "w") as d:
        d.write(bat_start_str)

    with open(inp_path.parent / stop_bat, "w") as d:
        d.write(f"cd /d {inp_path.parent}\nabaqus terminate job={analysis_name}")

    if manifest is not None:
        with open(inp_path.parent / "analysis_manifest.json", "w") as fp:
            json.dump(manifest, fp, indent=4)

    execute_path = _Settings.execute_dir if _Settings.execute_dir is not None else inp_path.parent

    if inp_path.parent != execute_path:
        os.makedirs(execute_path, exist_ok=True)
        shutil.copy(inp_path.parent / start_bat, execute_path / start_bat)
        shutil.copy(inp_path.parent / stop_bat, execute_path / stop_bat)

    if return_bat_str is True:
        return bat_start_str

    if execute is True:
        if run_ext is True:
            print(80 * "-")
            print(f'starting Abaqus simulation "{analysis_name}"')
            print(f"\nUsing the following parameters:\n{param_str}\n")
            subprocess.call("start " + start_bat, cwd=execute_path, shell=True)
            print("Note! This starts Abaqus in an external window on a separate thread.")
            print(80 * "-")
        else:
            print(80 * "-")
            print(f'starting Abaqus simulation "{analysis_name}"')
            print(f"\nUsing the following parameters:\n{param_str}\n")
            subprocess.call("start /wait " + start_bat, cwd=execute_path, shell=True)
            print(f'Finished Abaqus simulation "{analysis_name}"')
            print(80 * "-")

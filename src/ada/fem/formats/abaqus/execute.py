import pathlib
import shutil

from ada.fem.formats.utils import LocalExecute


def get_latest_version():
    abaqus_exe_path = shutil.which("abaqus")
    if abaqus_exe_path is None:
        raise FileNotFoundError("Unable to find abaqus in the system path")
    # go up to parent dir called EstProducts
    for parent in pathlib.Path(abaqus_exe_path).parents:
        if parent.name == "EstProducts":
            dir_names = [x.name for x in parent.iterdir()]
            return dir_names[0]


def run_abaqus(
    inp_path: pathlib.Path,
    cpus=2,
    gpus=None,
    run_ext=False,
    metadata=None,
    execute=True,
    exit_on_complete=True,
    run_in_shell=False,
):
    run_cmd = None
    custom_bat_str = None

    if metadata is None:
        metadata = {}

    aba_version = metadata.get("abaqus_version", get_latest_version())
    subr_path = metadata.get("subroutine_path", None)

    if subr_path is not None:
        if isinstance(subr_path, str):
            subr_path = pathlib.Path(subr_path).resolve().absolute()

        if subr_path.exists() is False:
            raise FileNotFoundError(f'Unable to find subroutine file "{subr_path}"')

        subroutine_entry_point_str = create_subroutine_input(inp_path, subr_path, aba_version)

        bat_file_path = (inp_path.parent / "abaqus").with_suffix(".bat")
        with open(bat_file_path, "w") as f:
            f.write(subroutine_entry_point_str)

        run_cmds = [bat_file_path.name, f"job={inp_path.stem}", f"CPUS={cpus}", f"user={subr_path.stem}", "interactive"]
        custom_bat_str = " ".join(run_cmds)

        if gpus is not None:
            custom_bat_str += f" GPUS={gpus}"

    aba_exe = AbaqusExecute(
        inp_path, cpus=cpus, run_ext=run_ext, metadata=metadata, auto_execute=execute, run_in_shell=run_in_shell
    )
    out = aba_exe.run(exit_on_complete, run_cmd=run_cmd, bat_start_str=custom_bat_str)

    return out


class AbaqusExecute(LocalExecute):
    def run(self, exit_on_complete=True, run_cmd=None, bat_start_str=None):
        from ada.fem.formats.general import FEATypes

        exe_path = self.get_exe(FEATypes.ABAQUS)
        gpus = "" if self._gpus is None else f"GPUS={self._gpus}"
        if run_cmd is None:
            run_cmd = f"{exe_path} job={self.analysis_name} CPUS={self._cpus}{gpus} interactive"
        stop_cmd = f"abaqus terminate job={self.analysis_name}"
        out = self._run_local(run_cmd, stop_cmd, exit_on_complete, bat_start_str)
        return out


def create_subroutine_input(inp_path, subroutine_path, aba_ver):
    subroutine_path = pathlib.Path(subroutine_path)
    shutil.copy(subroutine_path, inp_path.parent / subroutine_path.name)

    custom_bat_str = create_subroutine_entry_batch(aba_ver=aba_ver)
    return custom_bat_str


def create_subroutine_entry_batch(aba_ver):
    from ada.fem.formats.abaqus.config import AbaqusPaths

    aba_path = AbaqusPaths.abaqus_path_map(aba_ver)
    vs_vars_path = AbaqusPaths.vs_paths()
    intel_vars_path = AbaqusPaths.intel_fort_path()

    return rf"""@echo off
setlocal
set ABA_COMMAND=%~nx0
set ABA_COMMAND_FULL=%~f0
call "{vs_vars_path}\vcvars64.bat"
@call "{intel_vars_path}\vars.bat" -arch intel64 vs2022
"{aba_path}" %*
endlocal
"""

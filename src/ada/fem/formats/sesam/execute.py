import pathlib

from ada.config import logger
from ada.fem.formats.utils import LocalExecute, get_exe_path


def run_sesam(
    inp_path: pathlib.Path,
    cpus=2,
    gpus=None,
    run_ext=False,
    metadata=None,
    execute=True,
    exit_on_complete=True,
    run_in_shell=False,
):
    logger.info("sestra runs on single core only. changing cpus=1")
    cpus = 1
    ses_exe = SesamExecute(
        inp_path, cpus=cpus, run_ext=run_ext, metadata=metadata, auto_execute=execute, run_in_shell=run_in_shell
    )

    return ses_exe.run(exit_on_complete)


class SesamExecute(LocalExecute):
    def get_exe(self, fea_software):
        from ada.fem.exceptions import FEASolverNotInstalled
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
            from ada.fem.formats.sesam.sesam_exe_locator import (
                get_sestra_default_exe_path,
            )

            exe_path = get_sestra_default_exe_path()

        if exe_path is None:
            msg = (
                f'FEA Solver executable for "{solver_exe_name}" is not found. '
                f"Please make sure that an executable exists at the specified location.\n"
                f"See section about adding FEA solvers to paths "
                f"so that adapy finds them in the readme at https://github.com/Krande/adapy"
            )

            raise FEASolverNotInstalled(msg)

        return exe_path

    def run(self, exit_on_complete=True, run_cmd=None, bat_start_str=None):
        from ada.fem.formats.general import FEATypes

        exe_path = self.get_exe(FEATypes.SESAM)
        if run_cmd is None:
            run_cmd = f"{exe_path} /dsf {self.analysis_name}T100"
        stop_cmd = None
        out = self._run_local(run_cmd, stop_cmd, exit_on_complete, bat_start_str)
        return out

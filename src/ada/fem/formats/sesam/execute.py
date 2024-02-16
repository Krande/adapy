import pathlib

from ada.config import logger
from ada.fem.formats.utils import LocalExecute


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
    def run(self, exit_on_complete=True, run_cmd=None, bat_start_str=None):
        from ada.fem.formats.general import FEATypes

        exe_path = self.get_exe(FEATypes.SESAM)
        if run_cmd is None:
            run_cmd = f"{exe_path} /dsf {self.analysis_name}T100"
        stop_cmd = None
        out = self._run_local(run_cmd, stop_cmd, exit_on_complete, bat_start_str)
        return out

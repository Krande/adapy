import logging

from ada.fem.io.utils import LocalExecute, get_exe_path


def run_sesam(
    inp_path,
    cpus=2,
    gpus=None,
    run_ext=False,
    metadata=None,
    execute=True,
    exit_on_complete=True,
    run_in_shell=False,
):
    """

    :param inp_path: Path to input file folder(s)
    :param cpus: Number of CPUs to run the analysis on. Default is 2.
    :param gpus: Number of GPUs to run the analysis on. Default is none.
    :param run_ext: If False the process will wait for the abaqus analysis to finish. Default is False
    :param metadata: Dictionary containing various metadata relevant for the analysis
    :param execute: Automatically starts Abaqus analysis. Default is True
    :param exit_on_complete:
    :param run_in_shell:
    """
    run_cmd = None
    custom_bat_str = None

    aba_exe = SesamExecute(
        inp_path, cpus=cpus, run_ext=run_ext, metadata=metadata, auto_execute=execute, run_in_shell=run_in_shell
    )
    return aba_exe.run(exit_on_complete, run_cmd=run_cmd, bat_start_str=custom_bat_str)


class SesamExecute(LocalExecute):
    def run(self, exit_on_complete=True, run_cmd=None, bat_start_str=None):
        try:
            exe_path = get_exe_path("sestra")
        except FileNotFoundError as e:
            logging.error(e)
            return
        if run_cmd is None:
            run_cmd = f"{exe_path} /dsf {self.analysis_name}T100"
        stop_cmd = None
        out = self._run_local(run_cmd, stop_cmd, exit_on_complete, bat_start_str)
        return out

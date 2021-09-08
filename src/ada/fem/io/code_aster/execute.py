import logging
import pathlib

from ada.fem.io.utils import get_exe_path

from ..utils import LocalExecute


def write_to_log(res_str, fname):
    with open(f'timeit_{fname.split(".")[0]}.log', "a") as d:
        d.write("\n" + res_str)


def run_code_aster(
    inp_path,
    cpus=2,
    gpus=None,
    run_ext=False,
    metadata=None,
    execute=True,
    return_bat_str=False,
    exit_on_complete=True,
    run_in_shell=False,
):
    """

    TODO: Setup running for the code_aster docker image


    :param inp_path: Path to input file folder(s)
    :param cpus: Number of CPUs to run the analysis on. Default is 2.
    :param gpus: Number of GPUs to run the analysis on. Default is none.
    :param run_ext: If False the process will wait for the abaqus analysis to finish. Default is False
    :param metadata: Dictionary containing various metadata relevant for the analysis
    :param execute: Automatically starts Abaqus analysis. Default is True
    :param return_bat_str:
    :param exit_on_complete:
    :param run_in_shell:
    """
    from .writer import write_export_file

    name = pathlib.Path(inp_path).stem
    ca = CodeAsterExecute(
        inp_path,
        cpus=cpus,
        run_ext=run_ext,
        metadata=metadata,
        auto_execute=execute,
    )
    with open(inp_path, "w") as f:
        f.write(write_export_file(name, cpus))

    out = ca.run(exit_on_complete=exit_on_complete)
    return out


class CodeAsterExecute(LocalExecute):
    def run(self, exit_on_complete=True):
        try:
            exe_path = get_exe_path("code_aster")
        except FileNotFoundError as e:
            logging.error(e)
            return

        out = self._run_local(f'"{exe_path}" {self.analysis_name}.export', exit_on_complete=exit_on_complete)
        return out

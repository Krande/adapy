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
    """
    from .writer import write_export_file

    name = pathlib.Path(inp_path).stem
    ca = CodeAsterAnalysis(
        inp_path,
        cpus=cpus,
        run_ext=run_ext,
        metadata=metadata,
        execute=execute,
    )
    with open(inp_path, "w") as f:
        f.write(write_export_file(name, cpus))

    out = ca.run(exit_on_complete=exit_on_complete)
    return out


class CodeAsterAnalysis(LocalExecute):
    def __init__(self, inp_path, cpus=2, execute=True, metadata=None, local_execute=True, run_ext=True):
        """
        Code Aster Analysis

        Local Installation from:
        https://bitbucket.org/siavelis/codeaster-windows-src/downloads/code-aster_v2019_std-win64.zip

        :param inp_path:
        :param cpus:
        """
        super(CodeAsterAnalysis, self).__init__(
            inp_path,
            cpus,
            gpus=None,
            run_ext=run_ext,
            metadata=metadata,
            excute_locally=local_execute,
            auto_execute=execute,
        )

    def run(self, exit_on_complete=True):

        try:
            exe_path = get_exe_path("code_aster")
        except FileNotFoundError as e:
            logging.error(e)
            return

        out = self._run_local(f'"{exe_path}" {self.analysis_name}.export', exit_on_complete=exit_on_complete)
        return out

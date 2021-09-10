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

    return ca.run(exit_on_complete=exit_on_complete)


class CodeAsterExecute(LocalExecute):
    def run(self, exit_on_complete=True):
        try:
            exe_path = get_exe_path("code_aster")
        except FileNotFoundError as e:
            logging.error(e)
            return

        out = self._run_local(f'"{exe_path}" {self.analysis_name}.export', exit_on_complete=exit_on_complete)
        return out


def write_export_file(name: str, cpus: int):
    export_str = f"""P actions make_etude
P memory_limit 1274
P time_limit 900
P version stable
P mpi_nbcpu 1
P mode interactif
P ncpus {cpus}
F comm {name}.comm D 1
F mmed {name}.med D 20
F mess {name}.mess R 6
F rmed {name}.rmed R 80"""

    return export_str

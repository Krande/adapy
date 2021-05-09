import logging

from ada.config import Settings
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

    ca = CodeAsterAnalysis(
        inp_path,
        cpus=cpus,
        run_ext=run_ext,
        metadata=metadata,
        execute=execute,
    )
    ca.run(exit_on_complete=exit_on_complete)


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

    def _run_docker(self):
        raise NotImplementedError()
        # try:
        #     import docker
        # except ModuleNotFoundError as e:
        #     raise ModuleNotFoundError(
        #         "To use docker functionality you will need to install docker first.\n"
        #         'Use "pip install docker"\n\n'
        #         f'Original error message: "{e}"'
        #     )
        # cpu_count = 2
        #
        # calc_file = pathlib.Path(calc_file)
        # start_time = time.time()
        # environment = dict()
        # client.images.pull("quay.io/tianyikillua/code_aster", "latest")
        # container = client.containers.run(
        #     "quay.io/tianyikillua/code_aster",
        #     f"/home/aster/aster/bin/as_run {calc_file}",
        #     detach=True,
        #     working_dir="/home/calc/",
        #     environment=environment,
        #     cpu_count=cpu_count,
        #     volumes={work_dir: {"bind": "/home/calc/", "mode": "rw"}},
        # )
        #
        # for line in container.logs(stream=True):
        #     print(line.strip().decode("utf-8"))
        # end_time = time.time()
        # res_str = f"Analysis time {end_time - start_time:.2f}s"
        # print(res_str)

    def run(self, exit_on_complete=True):
        if Settings.use_docker_execute is False:
            try:
                exe_path = get_exe_path("code_aster")
            except FileNotFoundError as e:
                logging.error(e)
                return

            self._run_local(f'"{exe_path}" {self.analysis_name}.export', exit_on_complete=exit_on_complete)
        else:
            self._run_docker()

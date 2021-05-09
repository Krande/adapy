import logging
import pathlib
import time

from ada.config import Settings

from ..utils import LocalExecute, get_exe_path


class Calculix(LocalExecute):
    """

    :param inp_path: Path to input file folder(s)
    :param cpus: Number of CPUs to run the analysis on. Default is 2.
    :param gpus: Number of GPUs to run the analysis on. Default is none.
    :param run_ext: If False the process will wait for the abaqus analysis to finish. Default is False
    :param metadata: Dictionary containing various metadata relevant for the analysis
    :param execute: Automatically starts Abaqus analysis. Default is True


    Note!
        'analysis_name' be name of input file if 'analysis_path' is pointing to containing folder.
        Alternatively if name of input file and containing folder shares the same name 'analysis_path' can be set to the
        top folder and analysis_name will refer to the subfolder and inp-file sharing the same name.

    """

    def __init__(
        self,
        inp_path,
        cpus=2,
        gpus=None,
        run_ext=False,
        metadata=None,
        execute=True,
        execute_locally=True,
    ):
        super(Calculix, self).__init__(inp_path, cpus, gpus, run_ext, metadata, execute, execute_locally)
        self.inp_path = inp_path
        self._cpus = cpus
        self._run_ext = run_ext
        self._manifest = metadata
        self._execute = execute
        self._local_execute = execute_locally

    def run(self, exit_on_complete=True):
        if Settings.use_docker_execute is False:
            try:
                exe_path = get_exe_path("ccx")
            except FileNotFoundError as e:
                logging.error(e)
                return
            self._run_local(f"{exe_path} -i {self.analysis_name}", exit_on_complete=exit_on_complete)
        else:
            self._run_docker()

    def _run_docker(self):
        try:
            import docker
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                "To use docker functionality you will need to install docker first.\n"
                'Use "pip install docker"\n\n'
                f'Original error message: "{e}"'
            )

        client = docker.from_env()

        start_time = time.time()
        environment = dict(OMP_NUM_THREADS=f"{self._cpus}")
        container = client.containers.run(
            "ada/calculix",
            f"ccx_2.16 -i {self.analysis_name}",
            detach=True,
            working_dir="/home/calc/",
            environment=environment,
            cpu_count=self._cpus,
            volumes={str(self.analysis_dir): {"bind": "/home/calc/", "mode": "rw"}},
        )
        for line in container.logs(stream=True):
            print(line.strip().decode("utf-8"))
        end_time = time.time()
        res_str = f"Analysis time {end_time - start_time:.2f}s"
        print(res_str)

    @property
    def analysis_dir(self):
        return self.inp_path.parent

    @property
    def analysis_name(self):
        return self.inp_path.name.replace(".inp", "")

    @property
    def inp_path(self):
        return self._inp_path

    @inp_path.setter
    def inp_path(self, value):
        self._inp_path = pathlib.Path(value)


def run_calculix(inp_path, cpus=2, gpus=None, run_ext=False, manifest=None, execute=True, exit_on_complete=True):
    """

    :param inp_path: Destination path for Calculix input file
    :param cpus: Number of CPUS to use for analysis
    :param gpus: Number of GPUs to use for analysis
    :param run_ext: Run externally
    :param manifest:
    :param execute:
    :param exit_on_complete:
    :return:
    """
    from ccx2paraview import Converter

    inp_path = pathlib.Path(inp_path)

    ccx = Calculix(
        inp_path,
        cpus=cpus,
        gpus=gpus,
        run_ext=run_ext,
        metadata=manifest,
        execute=execute,
    )
    ccx.run(exit_on_complete=exit_on_complete)

    frd_file = inp_path.with_suffix(".frd")
    convert = Converter(str(frd_file), ["vtu"])
    convert.run()

import os
import pathlib
import shutil
import subprocess

from ..utils import LocalExecute


def write_to_log(res_str, fname):
    with open(f'timeit_{fname.split(".")[0]}.log', "a") as d:
        d.write("\n" + res_str)


def run_code_aster(
    inp_path,
    cpus=2,
    gpus=None,
    run_ext=False,
    manifest=None,
    subr_path=None,
    execute=True,
    return_bat_str=False,
    run_in_docker=False,
):
    """

    TODO: Setup running for the code_aster docker image


    :param inp_path: Path to input file folder(s)
    :param cpus: Number of CPUs to run the analysis on. Default is 2.
    :param gpus: Number of GPUs to run the analysis on. Default is none.
    :param run_ext: If False the process will wait for the abaqus analysis to finish. Default is False
    :param manifest: Dictionary containing various metadata relevant for the analysis
    :param subr_path: Path to fortran subroutine file (optional).
    :param execute: Automatically starts Abaqus analysis. Default is True
    :param return_bat_str:
    """
    work_dir = os.path.dirname(inp_path)
    ca = CodeAsterAnalysis(inp_path, work_dir)
    ca.run()


class CodeAsterAnalysis:
    exe_path = r"C:\code_aster\v2019\bin\as_run.bat"

    def __init__(self, inp_path, work_dir, cpus=2, local_execute=True, run_ext=True):
        """
        Code Aster Analysis

        Local Installation from:
        https://bitbucket.org/siavelis/codeaster-windows-src/downloads/code-aster_v2019_std-win64.zip

        :param inp_path:
        :param work_dir:
        :param cpus:
        """
        self._inp_path = inp_path
        self._work_dir = work_dir
        self._cpus = cpus
        self._run_ext = run_ext
        self._local_execute = local_execute

    def _run_local(self):
        """
        https://www.code-aster.org/forum2/viewtopic.php?id=13332

        :return:
        """

        analysis_dir = pathlib.Path(self.analysis_dir)

        bat_start_str = f"""echo OFF
for %%* in (.) do set CurrDirName=%%~nx*
title %CurrDirName%
cd /d {self.analysis_dir}
echo ON
{self.exe_path} {self.analysis_name}.export
pause"""
        start_bat = "run.bat"
        stop_bat = "stop.bat"
        execute_path = LocalExecute.execute_dir / self.analysis_name
        os.makedirs(execute_path, exist_ok=True)

        with open(os.path.join(execute_path, start_bat), "w") as d:
            d.write(bat_start_str + "\nEXIT")

        with open(os.path.join(execute_path, stop_bat), "w") as d:
            d.write(f"cd /d {self.analysis_dir}\nabaqus terminate job={self.analysis_name}")

        with open(analysis_dir / f"{self.analysis_name}.export", "w") as d:
            d.write(self.export_file)

        shutil.copy(
            os.path.join(execute_path, start_bat),
            os.path.join(self.analysis_dir, start_bat),
        )
        shutil.copy(
            os.path.join(execute_path, stop_bat),
            os.path.join(self.analysis_dir, stop_bat),
        )

        if self._run_ext is True:
            print(80 * "-")
            print('starting Calculix simulation "{}"'.format(self.analysis_name))
            subprocess.call("start " + start_bat, cwd=execute_path, shell=True)
            print("Note! This starts Calculix in an external window on a separate thread.")
            print(80 * "-")
        else:
            print(80 * "-")
            print('starting Calculix simulation "{}"'.format(self.analysis_name))
            subprocess.call("start /wait " + start_bat, cwd=execute_path, shell=True)
            print('Finished Calculix simulation "{}"'.format(self.analysis_name))
            print(80 * "-")

    def _run_docker(self):
        raise NotImplementedError()
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

    def run(self):
        if self._local_execute is True:
            self._run_local()
        else:
            self._run_docker()

    @property
    def export_file(self):
        """

        resources:

            https://www.code-aster.org/forum2/viewtopic.php?id=11949
            https://www.code-aster.org/doc/default/en/man_d/d1/d1.02.05.pdf

        :return:
        """
        return rf"""P actions make_etude
P memjob 507904
P memory_limit 496.0
P mode interactif
P mpi_nbcpu 1
P ncpus {self._cpus}
P rep_trav {self.analysis_dir}
P time_limit 60.0
P tpsjob 2
P version stable
A memjeveux 62.0
A tpmax 60.0
F comm {self.analysis_dir}\{self.analysis_name}.comm D  1
F mmed {self.analysis_dir}\{self.analysis_name}.med D  20
"""

    @property
    def analysis_dir(self):
        return pathlib.Path(self.inp_path).parent

    @property
    def analysis_name(self):
        return os.path.basename(str(self.inp_path).replace(".export", ""))

    @property
    def inp_path(self):
        return self._inp_path

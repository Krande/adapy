import logging
import pathlib

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
        try:
            exe_path = get_exe_path("ccx")
        except FileNotFoundError as e:
            logging.error(e)
            return
        out = self._run_local(f"{exe_path} -i {self.analysis_name}", exit_on_complete=exit_on_complete)
        return out

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


def run_calculix(inp_path, cpus=2, gpus=None, run_ext=False, metadata=None, execute=True, exit_on_complete=True):
    inp_path = pathlib.Path(inp_path)

    ccx = Calculix(
        inp_path,
        cpus=cpus,
        gpus=gpus,
        run_ext=run_ext,
        metadata=metadata,
        execute=execute,
    )
    return ccx.run(exit_on_complete=exit_on_complete)

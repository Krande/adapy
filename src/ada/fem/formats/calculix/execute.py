import pathlib

from ..utils import LocalExecute


def run_calculix(
    inp_path, cpus=2, gpus=None, run_ext=False, metadata=None, execute=True, exit_on_complete=True, run_in_shell=False
):
    inp_path = pathlib.Path(inp_path)

    ccx = CalculixExecute(
        inp_path,
        cpus=cpus,
        gpus=gpus,
        run_ext=run_ext,
        metadata=metadata,
        auto_execute=execute,
        run_in_shell=run_in_shell,
    )
    return ccx.run(exit_on_complete=exit_on_complete)


class CalculixExecute(LocalExecute):
    def run(self, exit_on_complete=True):
        from ada.fem.formats.general import FEATypes

        exe_path = self.get_exe(FEATypes.CALCULIX)
        out = self._run_local(f"{exe_path} -i {self.analysis_name}", exit_on_complete=exit_on_complete)
        return out

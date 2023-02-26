import pathlib
import re

from ada.config import get_logger
from ada.fem.formats.sesam.results.sin2sif import convert_sin_to_sif
from ada.fem.formats.utils import LocalExecute

logger = get_logger()


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
    out = ses_exe.run(exit_on_complete)
    reg_res = re.search(r"T([0-9]{0,4})\.FEM", inp_path.name)
    tbr = reg_res.group()
    num = reg_res.group(1)

    sin_file = inp_path.parent / inp_path.name.replace(tbr, f"R{num}.SIN")
    convert_sin_to_sif(sin_file)

    return out


class SesamExecute(LocalExecute):
    def run(self, exit_on_complete=True, run_cmd=None, bat_start_str=None):
        from ada.fem.formats.general import FEATypes

        exe_path = self.get_exe(FEATypes.SESAM)
        if run_cmd is None:
            run_cmd = f"{exe_path} /dsf {self.analysis_name}T100"
        stop_cmd = None
        out = self._run_local(run_cmd, stop_cmd, exit_on_complete, bat_start_str)
        return out

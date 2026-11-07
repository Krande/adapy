from __future__ import annotations

import os
import pathlib
import re
import subprocess

from ada.config import logger
from ada.fem.formats.utils import LocalExecute, get_exe_path


def run_sesam(
    inp_path: pathlib.Path,
    cpus=2,
    gpus=None,
    run_ext=False,
    metadata=None,
    execute=True,
    exit_on_complete=True,
    run_in_shell=False,
    timeout=None,
):
    logger.info("sestra runs on single core only. changing cpus=1")
    cpus = 1
    ses_exe = SesamExecute(
        inp_path, cpus=cpus, run_ext=run_ext, metadata=metadata, auto_execute=execute, run_in_shell=run_in_shell,
        timeout=timeout,
    )

    out = ses_exe.run(exit_on_complete)
    if isinstance(out, subprocess.CompletedProcess):
        check_sestra_run(pathlib.Path(inp_path).parent)
    return out


#: What Sestra writes last when a run succeeds: SESTRA.MLG's "Execution completed successfully." and SESTRA.LIS's
#: "Normal exit from Sestra." (V11.3-01). A failed run writes "Execution failed!" and "ERROR exit from Sestra."
_SUCCESS_MLG = "Execution completed successfully"
_SUCCESS_LIS = "Normal exit from Sestra"

#: The header of one SESTRA.MLG message: ``ERROR, Wed Nov  4 12:25:28 2026``. Its text is the lines after it, up to
#: the next blank line.
_MLG_ERROR = re.compile(r"^ERROR,\s")


def check_sestra_run(run_dir: str | os.PathLike) -> None:
    """Raise :class:`~ada.fem.exceptions.fea_execution.FEASolveFailed` unless Sestra says the run in ``run_dir``
    finished: "Execution completed successfully" in ``SESTRA.MLG`` or "Normal exit from Sestra" in ``SESTRA.LIS``.

    Sestra exits with code 0 when the analysis fails, and leaves a results file (``R1.SIN``) behind holding the model
    and no results, so neither the exit code nor the result file says anything. ``to_fem(..., execute=True)`` used to
    return normally after "Analysis failed", and the caller found out only when the results came back empty. The
    error carries the text of the first ``ERROR`` message of SESTRA.MLG: ``code`` is ``"execution_failed"``, or
    ``"no_sestra_log"`` when there is neither file (the run never started, or ran somewhere else).
    """
    from ada.fem.exceptions.fea_execution import FEASolveFailed

    run_dir = pathlib.Path(run_dir)
    mlg_path = run_dir / "SESTRA.MLG"
    lis_path = run_dir / "SESTRA.LIS"
    mlg = mlg_path.read_text(encoding="utf-8", errors="replace") if mlg_path.exists() else None
    lis = lis_path.read_text(encoding="utf-8", errors="replace") if lis_path.exists() else None
    if (mlg is not None and _SUCCESS_MLG in mlg) or (lis is not None and _SUCCESS_LIS in lis):
        return
    if mlg is None and lis is None:
        raise FEASolveFailed(
            "sestra", "no_sestra_log", run_dir, "neither SESTRA.MLG nor SESTRA.LIS: the run's verdict is unknown"
        )
    messages = _mlg_errors(mlg or "")
    message = messages[0] if messages else "Sestra did not report a successful run"
    raise FEASolveFailed(
        "sestra",
        "execution_failed",
        mlg_path if mlg is not None else lis_path,
        message,
        codes=["execution_failed"],
    )


def _mlg_errors(mlg: str) -> list[str]:
    """The text of each ``ERROR`` message in a SESTRA.MLG, in order."""
    out = []
    lines = mlg.splitlines()
    for i, line in enumerate(lines):
        if not _MLG_ERROR.match(line):
            continue
        body = []
        for nxt in lines[i + 1 :]:
            if not nxt.strip():
                break
            body.append(nxt.strip())
        out.append(" ".join(body))
    return out


class SesamExecute(LocalExecute):
    def get_exe(self, fea_software):
        from ada.fem.exceptions import FEASolverNotInstalled
        from ada.fem.formats.general import fem_solver_map

        solver_exe_name = fem_solver_map.get(fea_software, fea_software)
        exe_path = None
        for exe_test in [fea_software, solver_exe_name]:
            try:
                exe_path = get_exe_path(exe_test)
            except FileNotFoundError:
                continue
            if exe_path is not None:
                break
        if exe_path is None:
            from ada.fem.formats.sesam.sesam_exe_locator import (
                get_sestra_default_exe_path,
            )

            exe_path = get_sestra_default_exe_path()

        if exe_path is None:
            msg = (
                f'FEA Solver executable for "{solver_exe_name}" is not found. '
                f"Please make sure that an executable exists at the specified location.\n"
                f"See section about adding FEA solvers to paths "
                f"so that adapy finds them in the readme at https://github.com/Krande/adapy"
            )

            raise FEASolverNotInstalled(msg)

        return exe_path

    def run(self, exit_on_complete=True, run_cmd=None, bat_start_str=None):
        from ada.fem.formats.general import FEATypes

        exe_path = self.get_exe(FEATypes.SESAM)
        if run_cmd is None:
            run_cmd = f"{exe_path} /dsf {self.analysis_name}T100"
        stop_cmd = None
        out = self._run_local(run_cmd, stop_cmd, exit_on_complete, bat_start_str)
        return out

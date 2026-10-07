from __future__ import annotations

import os
import pathlib
import re
import subprocess

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
    out = ccx.run(exit_on_complete=exit_on_complete)
    if isinstance(out, subprocess.CompletedProcess):
        check_calculix_run(out, inp_path.parent / "run_log.txt", inp_path.parent / "spooles.out")
    return out


class CalculixExecute(LocalExecute):
    def run(self, exit_on_complete=True):
        from ada.fem.formats.general import FEATypes

        exe_path = self.get_exe(FEATypes.CALCULIX)
        out = self._run_local(f"{exe_path} -i {self.analysis_name}", exit_on_complete=exit_on_complete)
        return out


#: What is known to cause a CalculiX failure and what would fix it (measured on 2.23).
KNOWN_MESSAGES = {
    "spooles_singular": "the stiffness matrix is singular: a rigid-body motion is not held (a mechanism) -- check the "
    "supports and the connections",
    "e_c3d_u1": "a U1 beam cannot take that load (no body forces, no second order): see the CalculiX writer's "
    "findings",
    "no_job_finished": "CalculiX stopped without finishing (a crash, or an error it printed nowhere else)",
}

#: ``*ERROR in calinput: ...`` (code ``calinput``) or ``*ERROR reading *CLOAD: ...`` (code ``reading_CLOAD``).
_ERROR = re.compile(r"^\s*\*ERROR(?:\s+in\s+([A-Za-z0-9_]+)|\s+reading\s+\*([A-Za-z0-9_ ]+?))?\s*:\s*(.*)$")


def check_calculix_run(
    out: subprocess.CompletedProcess, log_path: str | os.PathLike, spooles_path: str | os.PathLike | None = None
) -> None:
    """Raise :class:`~ada.fem.exceptions.fea_execution.FEASolveFailed` unless CalculiX printed ``Job finished``
    and no ``*ERROR`` line.

    CalculiX prints its errors to standard output (``*ERROR in e_c3d_u1: no body forces``) and stops; a singular
    matrix it reports only in ``spooles.out`` ("matrix found to be singular", measured, 2.23), its output ending
    after "Factoring the system of equations". Either way it leaves a .frd file holding the model's header and no
    results, which the reader then fails on ("No element information from Calculix"). The captured output is written
    to ``log_path`` and named in the error.
    """
    from ada.fem.exceptions.fea_execution import FEASolveFailed

    text = "".join(s for s in (out.stdout, out.stderr) if s)
    errors = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _ERROR.match(line)
        if m is None:
            continue
        message = [line.strip()]
        for nxt in lines[i + 1 : i + 6]:  # ccx continues a message on indented lines
            if not nxt.startswith("   ") or not nxt.strip():
                break
            message.append(nxt.strip())
        code = m.group(1) or ("reading_" + m.group(2).strip().replace(" ", "_") if m.group(2) else "ERROR")
        errors.append((code, " ".join(s for s in message if s)))
    finished = "Job finished" in text
    if finished and not errors and out.returncode == 0:
        return
    log_path = pathlib.Path(log_path)
    log_path.write_text(text, encoding="utf-8")
    if not errors and spooles_path is not None:
        spooles = pathlib.Path(spooles_path)
        if spooles.exists() and "singular" in spooles.read_text(encoding="utf-8", errors="replace"):
            errors.append(("spooles_singular", f"{spooles.name}: matrix found to be singular"))
    if not errors:
        errors.append(("no_job_finished", f"no 'Job finished' in the output; exit code {out.returncode}"))
    codes = list(dict.fromkeys(code for code, _ in errors))
    code, message = errors[0]
    raise FEASolveFailed("calculix", code, log_path, message, KNOWN_MESSAGES.get(code, ""), codes)

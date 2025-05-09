from __future__ import annotations

import os
import pathlib
import subprocess

from ada.config import logger
from ada.core.file_system import get_short_path_name


def convert_sin_to_sif(sin_file: str | pathlib.Path, use_siu=False) -> None:
    if isinstance(sin_file, str):
        sin_file = pathlib.Path(sin_file)

    prepost_exe = os.environ.get("ADA_prepost_exe", None)
    if prepost_exe is None:
        from ada.fem.formats.sesam.sesam_exe_locator import get_prepost_default_exe_path

        prepost_exe = get_prepost_default_exe_path()

    if prepost_exe is None:
        raise FileNotFoundError("Prepost executable is not set. Please set it using `ADA_prepost_exe`")

    analysis_name = sin_file.stem
    formatting = "SIF-FORMATTED" if use_siu is False else "SIU-UNFORMATTED"
    jnl_file_str = f"OPEN SIN-DIRECT-ACCESS '' {analysis_name} OLD READ-ONLY\n"
    jnl_file_str += f"WRITE {formatting} '' {analysis_name} 1\nEND\nEXIT"

    with open(sin_file.parent / "run_prepost.jnl", "w") as f:
        f.write(jnl_file_str)

    run_params = "/NAME=PREPOST1/STAT=NEW/FORCED/LICENSE-WAIT=ON"
    log_params = f"more < {analysis_name}.JNL > log_{analysis_name}.log & echo finished prepost run 1"

    exe_str = f"set EXEPATH={get_short_path_name(str(prepost_exe))}"

    run_str = f"{exe_str}\nstart /w %EXEPATH% /INTER=L/COM-FI=run_prepost.jnl {run_params} & {log_params}"

    run_bat_file = (sin_file.parent / "run_sin2sif.bat").resolve().absolute()
    with open(run_bat_file, "w") as f:
        f.write(run_str)

    props = dict(shell=True, cwd=sin_file.parent, env=os.environ, universal_newlines=True)
    props["capture_output"] = True
    out = subprocess.run(str(run_bat_file), **props)
    logger.info(f'Finished SIN2SIF operation on "{analysis_name}"')

    res_str = str(out.stderr + out.stdout)
    with open(sin_file.parent / "run_prepost_log.txt", "w") as f:
        f.write(res_str)

    return out

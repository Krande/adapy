"""Run an OpenCourant analysis: the starter (model check + restart file) then the engine (time integration)."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

from ada.config import logger

from ..utils import LocalExecute, get_exe_path

#: Executable names of the conda-forge ``opencourant`` package (linux-64, gfortran build).
STARTER_EXE = "starter_linux64_gf"
ENGINE_EXE = "engine_linux64_gf"


def run_opencourant(
    inp_path, cpus=2, gpus=None, run_ext=False, metadata=None, execute=True, exit_on_complete=True, run_in_shell=False
):
    """``inp_path`` is the starter deck ``<name>_0000.rad`` (the engine deck sits beside it)."""
    exe = OpenCourantExecute(
        inp_path,
        cpus=cpus,
        gpus=gpus,
        run_ext=run_ext,
        metadata=metadata,
        auto_execute=execute,
        run_in_shell=run_in_shell,
    )
    return exe.run(exit_on_complete=exit_on_complete)


def _raise_stack_limit():  # pragma: no cover - runs in the child process
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    except (ValueError, OSError):
        pass


class OpenCourantExecute(LocalExecute):
    @property
    def run_name(self) -> str:
        stem = self.inp_path.stem
        return stem[: -len("_0000")] if stem.endswith("_0000") else stem

    def _env(self, starter: pathlib.Path) -> dict:
        env = dict(os.environ)
        env.setdefault("OMP_NUM_THREADS", str(self._cpus))
        env.setdefault("OMP_STACKSIZE", "400m")
        if "RAD_CFG_PATH" not in env:
            cfg = pathlib.Path(starter).resolve().parent.parent / "share" / "opencourant" / "hm_cfg_files"
            if cfg.is_dir():
                env["RAD_CFG_PATH"] = str(cfg)
        return env

    def run(self, exit_on_complete=True):
        starter = get_exe_path(STARTER_EXE)
        engine = get_exe_path(ENGINE_EXE)
        name = self.run_name
        if self.auto_execute is False:
            return None

        cwd = self.execute_dir
        env = self._env(starter)
        preexec = _raise_stack_limit if sys.platform.startswith("linux") else None
        props = dict(cwd=cwd, env=env, capture_output=True, text=True, errors="replace", preexec_fn=preexec)

        print(80 * "-")
        print(f'Starting OpenCourant simulation "{name}" using {self._cpus} threads')
        st = subprocess.run([str(starter), "-i", f"{name}_0000.rad", "-np", "1"], **props)
        (pathlib.Path(cwd) / "starter.log").write_text(st.stdout + st.stderr)
        if st.returncode != 0 or "ERROR TERMINATION" in st.stdout or "INPUT ERROR" in st.stdout:
            logger.error(f"OpenCourant starter failed, see {name}_0000.out")
            print(80 * "-")
            return st

        en = subprocess.run([str(engine), "-i", f"{name}_0001.rad", "-nt", str(self._cpus)], **props)
        (pathlib.Path(cwd) / "engine.log").write_text(en.stdout + en.stderr)
        if "NORMAL TERMINATION" not in en.stdout:
            logger.error(f"OpenCourant engine did not terminate normally, see {name}_0001.out")
        else:
            from .results.container import pack_radanim

            pack_radanim(cwd, name)
        print(f'Finished OpenCourant simulation "{name}"')
        print(80 * "-")
        return subprocess.CompletedProcess(en.args, en.returncode, st.stdout + en.stdout, st.stderr + en.stderr)

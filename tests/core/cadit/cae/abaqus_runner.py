"""Run an emitted Abaqus/CAE script and report what happened.

Two things make this less obvious than it looks, both measured on Abaqus 2025:

1. **``print`` output does not reach stdout.** It lands in ``abaqus.rpy`` in the working directory,
   each line prefixed ``#: ``. A harness that only reads stdout sees nothing a script said about
   itself, so the results have to be lifted out of the replay file.
2. **The exit status of ``abq2025.bat`` cannot be trusted.** A script that raises prints
   ``Abaqus Error: cae exited with an error`` to stdout, and the batch wrapper still returns 0. So
   failure is decided from the output, not the return code.

There are four CAE tokens on the site server, so every run here is bounded by a timeout and the
process is killed rather than left holding one.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass

#: Abaqus command scripts, newest first. `ADA_ABAQUS_CMD` overrides the search entirely.
ABAQUS_CANDIDATES = (
    r"C:\SIMULIA\Commands\abq2025.bat",
    r"C:\SIMULIA\Commands\abq2024.bat",
    r"C:\SIMULIA\Commands\abq2023.bat",
)

ERROR_MARKERS = ("Abaqus Error", "Abaqus/CAE Error")
LICENCE_MARKERS = (
    "Abaqus Error: Abaqus/CAE could not obtain a license",
    "licensing",
    "FlexNet",
    "Error: Abaqus/Analysis exited with error",
)
LICENCE_DENIED_MARKERS = (
    "could not obtain a license",
    "No licenses available",
    "license server",
)


#: Abaqus embeds its own Python, and `abq cae` in turn launches `abq standard` as a child, so the test
#: process's interpreter settings must not be handed to it. Measured, and reproducible both ways on the
#: same test selection: with the project's own `PYTHONPATH=src` inherited, `job.submit()` inside CAE
#: died with a bare `Abaqus Error: cae exited with an error` -- no traceback, no `.dat`, no `.msg`, the
#: analysis never started -- while the identical script run from a clean shell solved fine, and removing
#: `PYTHONPATH` from the child's environment made the whole selection pass. `PYTHONPATH=src` is
#: *relative*, which the child resolves against its own working directory. The rest are listed because
#: they are the same class of hazard, not because they were observed set.
_STRIPPED_ENV_VARS = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONOPTIMIZE",
    "PYTHONNOUSERSITE",
    "PYTHONEXECUTABLE",
    "PYTHONWARNINGS",
    "PYTHONIOENCODING",
)


def clean_environment() -> dict:
    env = dict(os.environ)
    for name in _STRIPPED_ENV_VARS:
        env.pop(name, None)
    return env


def abaqus_command() -> pathlib.Path | None:
    """The ``abqXXXX.bat`` to drive, or ``None`` if Abaqus is not installed here."""
    override = os.environ.get("ADA_ABAQUS_CMD")
    if override:
        candidate = pathlib.Path(override)
        return candidate if candidate.exists() else None
    for candidate in ABAQUS_CANDIDATES:
        path = pathlib.Path(candidate)
        if path.exists():
            return path
    found = shutil.which("abaqus")
    return pathlib.Path(found) if found else None


def abaqus_available() -> bool:
    return abaqus_command() is not None


@dataclass
class CaeRun:
    """What one ``abq cae noGUI=`` invocation did."""

    script: pathlib.Path
    workdir: pathlib.Path
    returncode: int
    stdout: str
    printed: list[str]
    timed_out: bool = False

    @property
    def licence_denied(self) -> bool:
        low = self.stdout.lower()
        return any(marker.lower() in low for marker in LICENCE_DENIED_MARKERS)

    @property
    def failed(self) -> bool:
        """The batch wrapper returns 0 even on a traceback, so the output decides."""
        if self.timed_out:
            return True
        if self.returncode != 0:
            return True
        return any(marker in self.stdout for marker in ERROR_MARKERS)

    @staticmethod
    def _matches(line: str, key: str) -> bool:
        """``key`` must be a whole leading token, so ``edge`` does not also match ``edges``."""
        return line == key or line.startswith(key + " ")

    def value(self, key: str) -> str:
        """The remainder of the single printed line whose leading token is ``key``."""
        matches = [line[len(key) :].strip() for line in self.printed if self._matches(line, key)]
        if len(matches) != 1:
            raise AssertionError(
                "expected exactly one printed line starting with {!r}, found {}:\n{}".format(
                    key, len(matches), "\n".join(self.printed)
                )
            )
        return matches[0]

    def values(self, key: str) -> list[str]:
        return [line[len(key) :].strip() for line in self.printed if self._matches(line, key)]

    def describe(self) -> str:
        return "returncode={}\ntimed_out={}\n--- stdout ---\n{}\n--- printed ---\n{}".format(
            self.returncode, self.timed_out, self.stdout, "\n".join(self.printed)
        )


def read_replay_prints(workdir: pathlib.Path) -> list[str]:
    """The ``#: ``-prefixed lines of ``abaqus.rpy`` -- everything the script printed."""
    rpy = workdir / "abaqus.rpy"
    if not rpy.exists():
        return []
    out = []
    for line in rpy.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#: "):
            out.append(line[3:])
        elif line.startswith("#:"):
            out.append(line[2:])
    return out


def run_cae_script(script: pathlib.Path, workdir: pathlib.Path | None = None, timeout: float = 900.0) -> CaeRun:
    """Copy ``script`` into ``workdir`` and run it through ``abq cae noGUI=``.

    The script is run with ``workdir`` as the working directory so its sidecars, ``abaqus.rpy`` and
    any job files land together and can be inspected afterwards.
    """
    command = abaqus_command()
    if command is None:
        raise RuntimeError("no Abaqus install found; guard licensed tests with abaqus_available()")

    script = pathlib.Path(script)
    workdir = pathlib.Path(workdir) if workdir is not None else script.parent
    workdir.mkdir(parents=True, exist_ok=True)
    local = workdir / script.name
    if script.resolve() != local.resolve():
        shutil.copyfile(script, local)

    # A stale replay file would be read as this run's output.
    for stale in workdir.glob("abaqus.rpy*"):
        stale.unlink()

    timed_out = False
    proc = subprocess.Popen(
        [str(command), "cae", "noGUI={}".format(local.name)],
        cwd=str(workdir),
        env=clean_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Do not leave a run holding one of the four CAE tokens.
        proc.kill()
        stdout, _ = proc.communicate()
        timed_out = True

    return CaeRun(
        script=local,
        workdir=workdir,
        returncode=proc.returncode,
        stdout=stdout or "",
        printed=read_replay_prints(workdir),
        timed_out=timed_out,
    )

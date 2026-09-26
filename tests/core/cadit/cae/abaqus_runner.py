"""Run an emitted Abaqus/CAE script and report what happened.

Three things make this less obvious than it looks, all measured on Abaqus 2025:

1. **``print`` output does not reach stdout.** It lands in ``abaqus.rpy`` in the working directory,
   each line prefixed ``#: ``. A harness that only reads stdout sees nothing a script said about
   itself, so the results have to be lifted out of the replay file.
2. **The exit status is useless in both directions.** ``abq2025.bat`` returns 0 whatever the CAE
   process it started did, so a non-zero status never arrives. And a ``sys.exit(1)`` inside the script
   is treated by CAE as a clean finish: no ``Abaqus Error`` line is printed at all. So the exit code
   cannot decide pass or fail, and neither can stdout alone.
3. **What can be trusted is what the script says about itself.** The writer prints an
   ``ADAPY-CAE BUILD OK:`` / ``ADAPY-CAE BUILD FAILED:`` banner and writes a
   ``<stem>.cae_build_result.json`` carrying an ``ok`` boolean, on success *and* on failure. This
   runner reads both, plus stdout's ``Abaqus Error`` for the crash case, and calls a run failed if any
   of them says so.

There are four CAE tokens on the site server, so every run here is bounded by a timeout and the
process is killed rather than left holding one.
"""

from __future__ import annotations

import json
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
#: The writer's own verdict, printed on the last line of a build.
BUILD_OK_BANNER = "ADAPY-CAE BUILD OK"
BUILD_FAILED_BANNER = "ADAPY-CAE BUILD FAILED"
#: Any script may leave one of these; the writer always does, on success and on failure alike.
RESULT_SIDECAR_GLOB = "*.cae_build_result.json"
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
    def build_results(self) -> list[dict]:
        """Every ``<stem>.cae_build_result.json`` the run left behind, parsed."""
        results = []
        for path in sorted(self.workdir.glob(RESULT_SIDECAR_GLOB)):
            try:
                results.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:  # a truncated sidecar is itself a failure
                results.append({"ok": False, "errors": ["unreadable sidecar {}: {}".format(path.name, exc)]})
        return results

    @property
    def sidecar_says_failed(self) -> bool:
        for result in self.build_results:
            if result.get("ok") is False or result.get("error") or result.get("errors"):
                return True
        return False

    @property
    def failure_signals(self) -> list[str]:
        """Which of the four independent signals said this run failed. Empty means a clean run.

        Kept as a list rather than a bool so a test can assert *which* signal fired -- the point being
        that the exit status is not one that can be relied on.
        """
        signals = []
        if self.timed_out:
            signals.append("timed out")
        if self.returncode != 0:
            signals.append("returncode {}".format(self.returncode))
        if any(marker in self.stdout for marker in ERROR_MARKERS):
            signals.append("Abaqus Error on stdout")
        if any(line.startswith(BUILD_FAILED_BANNER) for line in self.printed):
            signals.append("BUILD FAILED banner")
        if self.sidecar_says_failed:
            signals.append("build-result sidecar")
        return signals

    @property
    def failed(self) -> bool:
        """``abq.bat`` returns 0 whatever happened, and a `sys.exit(1)` prints nothing, so ask everyone."""
        return bool(self.failure_signals)

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
        return "returncode={} (never meaningful) signals={}\n--- stdout ---\n{}\n--- printed ---\n{}".format(
            self.returncode, self.failure_signals, self.stdout, "\n".join(self.printed)
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
    any job files land together and can be inspected afterwards. Any ``<stem>*.sat`` beside the
    source script is copied too: a plate model imports its geometry from one.
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
        # The script imports its plates from an ACIS body written beside it, resolved relative to
        # itself -- so moving the script without them leaves a run that dies in the kernel on the
        # first plate. Every sidecar the writer produced travels with it.
        for sidecar in sorted(script.parent.glob(script.stem + "*.sat")):
            shutil.copyfile(sidecar, workdir / sidecar.name)

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

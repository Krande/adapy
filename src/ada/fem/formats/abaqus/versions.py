"""Abaqus version probe + executable path resolution.

The probe shells out to `abaqus information=release` and parses the
version line; cheap enough to call per-build, but the verification
report caches the result in `<doc>/.cache/software_versions.json` to
keep cache-only docs builds offline.

The executable comes from the shared lookup (`resolve_solver_exe`), which
reads `[fea] fem_exe_paths.abaqus` first. None means callers treat abaqus
as not available.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Optional


def get_abaqus_exe() -> Optional[pathlib.Path]:
    """Where abaqus is, or None, which tells the consumer to skip the abaqus branch entirely.

    A path from `Config` comes back even when no file is there; callers that need that checked call `.exists()`.
    """
    from ada.fem.formats.utils import resolve_solver_exe

    return resolve_solver_exe("abaqus")


def get_abaqus_version(exe: Optional[pathlib.Path] = None) -> str:
    """Probe abaqus for its release version. Raises if `exe` is None
    or the subprocess fails.

    Parses lines like `Abaqus 2024 RELr1 2024_06_12-04.13.10-1`; returns
    `"2024 (RELr1 2024_06_12-04.13.10-1)"`.
    """
    exe = exe or get_abaqus_exe()
    if exe is None:
        raise FileNotFoundError("abaqus was not found: set [fea] fem_exe_paths.abaqus in ada_config.toml")
    proc = subprocess.run(
        [exe.as_posix(), "information=release"],
        text=True,
        capture_output=True,
        # The legacy probe used shell=True; keep that for compatibility
        # — some Abaqus installs bundle a .bat wrapper that doesn't
        # exec directly on Windows.
        shell=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"abaqus version probe failed: {proc.stderr}")
    pattern = re.compile(
        r"(?<=Abaqus\s)(?P<version>\d{4}).*?(?P<release>RELr\d+\s\d+)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(proc.stdout)
    if match is None:
        raise RuntimeError(f"abaqus version probe returned unparseable output:\n{proc.stdout}")
    return f"{match.group('version')} ({match.group('release')})"

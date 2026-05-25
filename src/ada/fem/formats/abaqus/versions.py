"""Abaqus version probe + executable path resolution.

The probe shells out to `abaqus information=release` and parses the
version line; cheap enough to call per-build, but the verification
report caches the result in `<doc>/.cache/software_versions.json` to
keep cache-only docs builds offline.

Executable path is resolved from `ADA_abaqus_exe` (the env-var
convention used elsewhere in adapy). Returns None if unset — callers
treat that as "abaqus not available".
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from typing import Optional


def get_abaqus_exe() -> Optional[pathlib.Path]:
    """Resolve the abaqus executable path from `ADA_abaqus_exe`.

    Returns None when the env var isn't set — that's the signal the
    consumer should skip the abaqus branch entirely. Returns a Path
    even when the file doesn't exist on disk; callers that need
    existence check can call `.exists()` themselves.
    """
    raw = os.getenv("ADA_abaqus_exe")
    if raw is None:
        return None
    return pathlib.Path(raw)


def get_abaqus_version(exe: Optional[pathlib.Path] = None) -> str:
    """Probe abaqus for its release version. Raises if `exe` is None
    or the subprocess fails.

    Parses lines like `Abaqus 2024 RELr1 2024_06_12-04.13.10-1`; returns
    `"2024 (RELr1 2024_06_12-04.13.10-1)"`.
    """
    exe = exe or get_abaqus_exe()
    if exe is None:
        raise FileNotFoundError("ADA_abaqus_exe is not set")
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
        raise RuntimeError(
            f"abaqus version probe returned unparseable output:\n{proc.stdout}"
        )
    return f"{match.group('version')} ({match.group('release')})"

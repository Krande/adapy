"""Drive DNV GeniE to import an adapy-written concept XML.

Two entry points:

* :func:`open_in_genie` — launch the GeniE GUI on a concept XML (interactive).
* :func:`verify_genie_import` — run the headless ``GenieRuntime.exe`` on a
  concept XML, capture its console output, and report whether the import
  succeeded. This is what lets a roundtrip be checked without a human clicking
  through the importer, e.g. bisecting which plates make ACIS reject a file.

Both build a ``startup.js`` that runs ``ImportConceptXml().DoImport(...)`` and
save. The only license feature a concept-geometry import needs is
``CurvedGeometry`` — the runtime
otherwise tries to check out *every* feature (``--licenses`` default is ``all``)
and fails on the first one the site is not entitled to.
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess

from ada.config import logger
from ada.fem.formats.sesam.sesam_exe_locator import (
    get_genie_default_exe_path,
    get_genie_runtime_default_exe_path,
)

DEFAULT_LICENSES = "CurvedGeometry"

# Substrings that mark a failed headless import in GeniE's console output. The
# ACIS relink failure (error 21013) is reported inline rather than raising, so
# the process can still exit 0 — the text is the only signal.
_LICENSE_MARKERS = ("License Error",)
_IMPORT_ERROR_MARKERS = ("Internal ACIS error", "ACIS error", "Input error")


def _startup_js(gxml: pathlib.Path, extra_js: str) -> str:
    return (
        'GenieRules.Compatibility.version = "V9.1-00";\n'
        "GenieRules.Tolerances.useTolerantModelling = true;\n"
        "GenieRules.Tolerances.angleTolerance = 2 deg;\n"
        "XmlImporter = ImportConceptXml();\n"
        "XmlImporter.UseFastFaceting = true;\n"
        f'XmlImporter.DoImport("{gxml.as_posix()}");\n'
        "Save();\n" + (extra_js or "")
    )


@dataclasses.dataclass
class GenieImportResult:
    """Outcome of a headless GeniE import."""

    success: bool
    returncode: int | None
    stdout: str
    stderr: str
    error_kind: str | None  # "license" | "import" | "timeout" | "no-exe" | None
    error_detail: str | None

    def __bool__(self) -> bool:
        return self.success


def _classify(stdout: str, stderr: str) -> tuple[str | None, str | None]:
    blob = f"{stdout}\n{stderr}"
    for marker in _LICENSE_MARKERS:
        if marker in blob:
            return "license", _first_line_with(blob, marker)
    for marker in _IMPORT_ERROR_MARKERS:
        if marker in blob:
            return "import", _first_line_with(blob, marker)
    return None, None


def _first_line_with(blob: str, marker: str) -> str:
    for line in blob.splitlines():
        if marker in line:
            return line.strip()
    return marker


def verify_genie_import(
    gxml: str | pathlib.Path,
    workspace: str | pathlib.Path | None = None,
    licenses: str = DEFAULT_LICENSES,
    extra_js: str = "",
    timeout: float | None = 600.0,
    exe_path: str | None = None,
) -> GenieImportResult:
    """Import ``gxml`` with headless GenieRuntime and report success.

    Success means the runtime ran the import script to completion without a
    license failure or an ACIS/input error in its console output. A curved
    concept import needs only the ``CurvedGeometry`` license feature.
    """
    gxml = pathlib.Path(gxml).resolve()
    exe = exe_path or get_genie_runtime_default_exe_path()
    if exe is None or not pathlib.Path(exe).is_file():
        return GenieImportResult(
            False, None, "", "", "no-exe",
            "GenieRuntime.exe not found; set ADA_GENIE_RUNTIME_EXE",
        )

    if workspace is None:
        workspace = gxml.parent / "genie_ws" / gxml.stem
    workspace = pathlib.Path(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)

    startup = workspace.parent / f"{gxml.stem}_startup.js"
    startup.write_text(_startup_js(gxml, extra_js))

    args = [str(exe), str(workspace.absolute()), "--new", "--javascript_execution_policy=unsafe"]
    if licenses:
        args += ["--licenses", licenses]
    args += [f"--com={startup.absolute()}", "--exit"]

    logger.info(f"genie-verify: {' '.join(args)}")
    try:
        proc = subprocess.run(
            args,
            cwd=str(pathlib.Path(exe).parent),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return GenieImportResult(False, None, e.stdout or "", e.stderr or "", "timeout", f"timed out after {timeout}s")

    kind, detail = _classify(proc.stdout, proc.stderr)
    success = kind is None and proc.returncode == 0
    return GenieImportResult(success, proc.returncode, proc.stdout, proc.stderr, kind, detail)


def open_in_genie(
    gxml: str | pathlib.Path,
    workspace: str | pathlib.Path | None = None,
    licenses: str = DEFAULT_LICENSES,
    extra_js: str = "",
    run_externally: bool = True,
    exe_path: str | None = None,
) -> None:
    """Open ``gxml`` in the GeniE GUI (interactive).

    ``run_externally`` (default) returns immediately after launching; set False
    to block until GeniE exits. Use :func:`verify_genie_import` instead when you
    only need a pass/fail on the import.
    """
    gxml = pathlib.Path(gxml).resolve()
    exe = exe_path or get_genie_default_exe_path()
    if exe is None or not pathlib.Path(exe).is_file():
        raise FileNotFoundError("GeniE GUI exe not found; set ADA_GENIE_EXE")

    if workspace is None:
        workspace = gxml.parent / "genie_ws" / gxml.stem
    workspace = pathlib.Path(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)

    startup = workspace.parent / f"{gxml.stem}_startup.js"
    startup.write_text(_startup_js(gxml, extra_js))

    # GUI GeniE takes the /FLAG form.
    args = [str(exe), str(workspace.absolute()), "/new", "/javascript_execution_policy=unsafe"]
    if licenses:
        args.append(f"/licenses={licenses}")
    args.append(f"/com={startup.absolute()}")

    if run_externally:
        subprocess.Popen(args, cwd=str(pathlib.Path(exe).parent))
    else:
        subprocess.run(args, cwd=str(pathlib.Path(exe).parent))

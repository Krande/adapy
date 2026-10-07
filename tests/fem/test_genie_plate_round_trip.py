"""GeniE in the loop: a GeniE plate goes through adapy and back as the same GeniE plate.

Needs GeniE V9.3 or later (``GenieRuntime.exe``, headless; the twins' binary bodies need V9.3).
For each committed GeniE V9.3 twin, GeniE imports (``WorkspaceImporter``) the original and
adapy's writes of it -- text and binary body, read from the text and from the binary twin --
exports its concept XML and prints every plate's ``area``. adapy's write must read in GeniE
as the original does: the same plate elements, kinds and names, over the same number of
faces, with the same GeniE-measured area to the digit GeniE prints.

GeniE's ``area`` of a curved plate is its facetted area, not the exact one: the quarter
cylinder of radius 1 and height 2 prints 3.136548491 against pi = 3.14159265 -- 8 chords over
90 degrees (2 * 8 * sin(pi / 32) * 2 = 3.1365485). It is therefore compared GeniE-to-GeniE,
which is exact, and adapy's own exact area separately.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

import ada


def _genie_runtime() -> str | None:
    from ada.fem.formats.sesam.sesam_exe_locator import (
        get_genie_runtime_default_exe_path,
    )

    try:
        exe = get_genie_runtime_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None
    if exe is None:
        return None
    version = re.search(r"GeniE V(\d+)\.(\d+)", str(exe))
    if version is None or (int(version.group(1)), int(version.group(2))) < (9, 3):
        return None
    return exe


EXE = _genie_runtime()
pytestmark = pytest.mark.skipif(EXE is None, reason="GeniE V9.3 or later is not installed")

PLATE_TAGS = ("flat_plate", "curved_shell")

#: twin -> the plates GeniE holds in it, as (element, name, faces)
MODELS = {
    "plate_t_junction": [("flat_plate", "Pl1", 3)],
}


def _run(workdir: pathlib.Path, js: str) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "run.js"
    script.write_text(js)
    args = [
        EXE,
        str(workdir / "ws" / "ws"),
        "--new",
        "--javascript_execution_policy=unsafe",
        f"--com={script}",
        "--exit",
    ]
    p = subprocess.run(args, cwd=str(pathlib.Path(EXE).parent), capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-1000:]
    return p.stdout


def _genie_plates(gnx: pathlib.Path, workdir: pathlib.Path) -> dict[str, tuple[str, int, str]]:
    """GeniE's reading of ``gnx``: plate name -> (element, number of faces, printed area)."""
    xml = workdir / "concept.xml"
    imp = f'importer = WorkspaceImporter();\nimporter.DoImport("{gnx.as_posix()}");\n'
    _run(workdir / "export", imp + f'ExportConceptXml().DoExport("{xml.as_posix()}");\n')
    text = xml.read_text(errors="replace")
    plates = {}
    for m in re.finditer(r'<(flat_plate|curved_shell) name="([^"]+)"(.*?)</\1>', text, re.S):
        plates[m.group(2)] = (m.group(1), len(re.findall(r"<face ", m.group(3))))
    out = _run(workdir / "area", imp + "".join(f'print("AREA {n} " + {n}.area);\n' for n in sorted(plates)))
    areas = dict(re.findall(r"->AREA (\S+) (\S+) m\^2", out))
    return {name: (*plates[name], areas.get(name)) for name in plates}


@pytest.fixture
def genie93(fem_files) -> pathlib.Path:
    return fem_files / "sesam" / "genie93_acis"


@pytest.mark.parametrize("model", sorted(MODELS))
def test_plates_read_in_genie_as_the_original(genie93, model, tmp_path):
    original = _genie_plates(genie93 / f"{model}_text.gnx", tmp_path / "genie")
    assert [(kind, name, faces) for name, (kind, faces, _area) in sorted(original.items())] == MODELS[model]
    assert all(area is not None for *_rest, area in original.values())

    routes = {}
    for body in ("text", "binary"):
        a = ada.from_gnx(genie93 / f"{model}_{body}.gnx")
        for out in ("text", "binary"):
            routes[f"{body}->{out}"] = a.to_gnx(
                tmp_path / f"{body}_{out}" / f"{model}.gnx", binary_acis=out == "binary"
            )
    read = {route: _genie_plates(gnx, tmp_path / route.replace("->", "_")) for route, gnx in routes.items()}
    assert read == {route: original for route in routes}

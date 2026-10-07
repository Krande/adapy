"""GeniE in the loop: binary ACIS workspaces through adapy and back into GeniE V9.3.

Needs GeniE V9.3 or later (``GenieRuntime.exe``, headless): earlier versions cannot read a binary
body at all. Each case has GeniE import a workspace with ``WorkspaceImporter`` into a new
workspace and export its concept XML, and compares what GeniE read:

* GeniE binary -> adapy -> binary -> GeniE, against GeniE's own reading of the original;
* GeniE text -> adapy -> binary -> GeniE, and binary -> adapy -> text -> GeniE, against the first;
* an adapy binary workspace *opened* in GeniE and saved again stays binary (the
  ``WriteACISBinaryFile`` option adapy sets is what keeps it so: measured, without it GeniE
  re-saves text).

The models are the committed GeniE V9.3 twins (the plate round trips themselves -- areas,
meshes, the hole -- are ``test_genie_plate_round_trip``).
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import zipfile

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

TAGS = ("straight_beam", "flat_plate", "curved_shell", "loadcase_basic", "loadcase_combination", "point_mass")
# surface_load is not counted: adapy's reader omits, by name, the varying/component/polygon pressures
# it cannot hold (4 of the 6 in loads_all_kinds), in text and binary alike -- not a format matter.
TAGS += ("support_point", "point_load", "line_load")
MODELS = ["plate", "beams_on_plate", "curved_plates_resaved", "loads_all_kinds", "cylinder_shell", "plate_with_hole"]


def _run(workdir: pathlib.Path, js: str, workspace: list[str]) -> str:
    script = workdir / "run.js"
    script.write_text(js)
    args = [EXE, *workspace, "--javascript_execution_policy=unsafe", f"--com={script}", "--exit"]
    p = subprocess.run(args, cwd=str(pathlib.Path(EXE).parent), capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-1000:]
    return p.stdout


def _genie_reads(gnx: pathlib.Path, workdir: pathlib.Path) -> dict[str, int]:
    """What GeniE V9.3 imports from ``gnx``: element counts of its concept-XML export."""
    workdir.mkdir(parents=True)
    xml = workdir / "concept.xml"
    js = (
        f'importer = WorkspaceImporter();\nimporter.DoImport("{gnx.as_posix()}");\n'
        f'ExportConceptXml().DoExport("{xml.as_posix()}");\n'
    )
    _run(workdir, js, [str(workdir / "ws" / "ws"), "--new"])
    text = xml.read_text(errors="replace")
    return {tag: len(re.findall(rf"<{tag}[ >/]", text)) for tag in TAGS}


@pytest.fixture
def genie93(fem_files) -> pathlib.Path:
    return fem_files / "sesam" / "genie93_acis"


@pytest.mark.parametrize("model", MODELS)
def test_binary_round_trips_read_in_genie_as_the_original(genie93, model, tmp_path):
    original = _genie_reads(genie93 / f"{model}_binary.gnx", tmp_path / "genie")
    assert sum(original.values()) > 0

    from_binary = ada.from_gnx(genie93 / f"{model}_binary.gnx")
    from_text = ada.from_gnx(genie93 / f"{model}_text.gnx")
    routes = {
        "binary->binary": from_binary.to_gnx(tmp_path / "bb" / f"{model}.gnx", binary_acis=True),
        "text->binary": from_text.to_gnx(tmp_path / "tb" / f"{model}.gnx", binary_acis=True),
        "binary->text": from_binary.to_gnx(tmp_path / "bt" / f"{model}.gnx"),
    }
    read = {name: _genie_reads(gnx, tmp_path / name.replace("->", "_")) for name, gnx in routes.items()}
    assert read == {name: original for name in routes}


def test_genie_keeps_an_adapy_binary_workspace_binary_on_save(genie93, tmp_path):
    gnx = ada.from_gnx(genie93 / "beams_on_plate_text.gnx").to_gnx(tmp_path / "w.gnx", binary_acis=True)
    wsdir = tmp_path / "open"
    wsdir.mkdir()
    shutil.copy(gnx, wsdir / "w.gnx")
    xml = wsdir / "concept.xml"
    _run(wsdir, f'ExportConceptXml().DoExport("{xml.as_posix()}");\n', [str(wsdir / "w.gnx")])
    with zipfile.ZipFile(wsdir / "w.gnx") as z:
        names = z.namelist()
        model_xml = z.read("modelData.xml")
    assert "acisGeometry.sab" in names and "acisGeometry.sat" not in names
    assert model_xml.count(b'<option value="true" option="WriteACISBinaryFile" />') == 2, "GeniE rewrote it"
    assert len(re.findall(r"<flat_plate[ >]", xml.read_text(errors="replace"))) == 1

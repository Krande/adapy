"""GeniE in the loop: a GeniE plate goes through adapy and back as the same GeniE plate.

Needs GeniE V9.3 or later (``GenieRuntime.exe``, headless; the twins' binary bodies need V9.3).
For each committed GeniE V9.3 twin GeniE imports (``WorkspaceImporter``) the original and
adapy's writes of it -- text and binary body, read from the text and from the binary twin --
exports its concept XML and prints every plate's ``area``; and it meshes the original and one
of adapy's writes. adapy's write must read in GeniE as the original does: the same plate
elements, kinds and names over the same number of faces, the same GeniE-measured area to the
digit GeniE prints, and the same mesh: every card identical bar the date, node coordinates to a
micrometre -- which pins the geometry, the normals (element node order) and the hole at once.

GeniE's ``area`` of a curved plate is its facetted area, not the exact one: the quarter
cylinder of radius 1 and height 2 prints 3.136548491 against pi = 3.14159265 -- 8 chords over
90 degrees (2 * 8 * sin(pi / 32) * 2 = 3.1365485). It is therefore compared GeniE-to-GeniE.

The plate with a hole is the one case whose concept read differs, by design: GeniE holds the
hole as a ``<hole>`` concept over a disc face that its plate also names (GeniE's ``area`` of the
plate is the whole 12 m2), and adapy reads the hole into the plate's geometry instead -- the
face around it, with the hole as an inner loop. GeniE re-imports that as one ``flat_plate`` over
one face whose area excludes the hole, no ``<hole>`` concept, and meshes it identically.
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

#: twin -> the plates GeniE holds in the original, as (element, name, faces)
MODELS = {
    "plate_t_junction": [("flat_plate", "Pl1", 3)],
    "cylinder_shell": [("curved_shell", "Sh1", 1)],
    "swept_arc_shell": [("curved_shell", "Sh1", 1)],
    "plate_with_hole": [("flat_plate", "Pl1", 2)],
}

#: what GeniE reads from adapy's write where it is not the original, with why (see the module doc)
ADAPY_READ = {
    "plate_with_hole": {"Pl1": ("flat_plate", 1, "11.50058847")},
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


def _import(gnx: pathlib.Path) -> str:
    return f'importer = WorkspaceImporter();\nimporter.DoImport("{gnx.as_posix()}");\n'


def _genie_plates(gnx: pathlib.Path, workdir: pathlib.Path) -> tuple[dict[str, tuple[str, int, str]], int]:
    """GeniE's reading of ``gnx``: plate name -> (element, faces, printed area), and its hole count."""
    xml = workdir / "concept.xml"
    _run(workdir / "export", _import(gnx) + f'ExportConceptXml().DoExport("{xml.as_posix()}");\n')
    text = xml.read_text(errors="replace")
    plates = {}
    for m in re.finditer(r'<(flat_plate|curved_shell) name="([^"]+)"(.*?)</\1>', text, re.S):
        plates[m.group(2)] = (m.group(1), len(re.findall(r"<face ", m.group(3))))
    out = _run(workdir / "area", _import(gnx) + "".join(f'print("AREA {n} " + {n}.area);\n' for n in sorted(plates)))
    areas = dict(re.findall(r"->AREA (\S+) (\S+) m\^2", out))
    return {name: (*plates[name], areas.get(name)) for name in plates}, len(re.findall(r"<hole[ >]", text))


def _genie_mesh(gnx: pathlib.Path, workdir: pathlib.Path) -> list[str]:
    """GeniE's 0.25 m mesh of ``gnx`` as a Sesam FEM file, without its date line."""
    fem = workdir / "T1.FEM"
    js = (
        _import(gnx) + "Md = MeshDensity(0.25 m);\nMd.setDefault();\nAnalysis1 = Analysis(true);\n"
        f'Analysis1.add(MeshActivity());\nAnalysis1.execute();\nExportMeshFem().DoExport("{fem.as_posix()}");\n'
    )
    _run(workdir, js)
    return [line for line in fem.read_text().splitlines() if "DATE:" not in line]


@pytest.fixture
def genie93(fem_files) -> pathlib.Path:
    return fem_files / "sesam" / "genie93_acis"


@pytest.mark.parametrize("model", sorted(MODELS))
def test_plates_read_in_genie_as_the_original(genie93, model, tmp_path):
    original, holes = _genie_plates(genie93 / f"{model}_text.gnx", tmp_path / "genie")
    assert [(kind, name, faces) for name, (kind, faces, _area) in sorted(original.items())] == MODELS[model]
    assert all(area is not None for *_rest, area in original.values())
    assert holes == (1 if model == "plate_with_hole" else 0)

    routes = {}
    for body in ("text", "binary"):
        a = ada.from_gnx(genie93 / f"{model}_{body}.gnx")
        for out in ("text", "binary"):
            routes[f"{body}->{out}"] = a.to_gnx(
                tmp_path / f"{body}_{out}" / f"{model}.gnx", binary_acis=out == "binary"
            )
    read = {route: _genie_plates(gnx, tmp_path / route.replace("->", "_")) for route, gnx in routes.items()}
    expected = (ADAPY_READ.get(model, original), 0)
    assert read == {route: expected for route in routes}

    mesh_original = _genie_mesh(genie93 / f"{model}_text.gnx", tmp_path / "mesh_genie")
    mesh_adapy = _genie_mesh(routes["binary->binary"], tmp_path / "mesh_adapy")
    assert len(mesh_original) > 10
    _assert_same_mesh(mesh_adapy, mesh_original)


def _assert_same_mesh(got: list[str], want: list[str]) -> None:
    """Every card identical, the node coordinates to a micrometre.

    Measured: on the three single-face twins the two FEM files are identical line for line; on
    plate_t_junction 3 of 221 interior nodes of GeniE's free mesh move by 1.2e-7 m (the last
    printed digit) while every element, node number and boundary node is identical -- the
    smoothing feels the faces' parameterisation, which is adapy's rather than GeniE's.
    """
    assert len(got) == len(want)
    for g, w in zip(got, want):
        if g.startswith("GCOORD") and w.startswith("GCOORD"):
            gv, wv = [float(x) for x in g.split()[1:]], [float(x) for x in w.split()[1:]]
            assert gv[0] == wv[0] and max(abs(a - b) for a, b in zip(gv[1:], wv[1:])) <= 1e-6, (g, w)
        else:
            assert g == w


def _mesh_stats(fem_lines: list[str], tmp_path: pathlib.Path, centre, radius) -> tuple[int, float, int]:
    """(shell elements, their total area, elements centred inside the hole) of a Sesam FEM mesh."""
    import numpy as np

    tmp_path.mkdir(parents=True, exist_ok=True)
    fem = tmp_path / "stats.FEM"
    fem.write_text("\n".join(fem_lines) + "\n")
    a = ada.from_fem(fem)
    n = inside = 0
    area = 0.0
    for p in a.get_all_parts_in_assembly():
        for el in p.fem.elements.shell:
            xyz = np.asarray([nd.p for nd in el.nodes], dtype=float)
            area += sum(
                0.5 * np.linalg.norm(np.cross(xyz[i] - xyz[0], xyz[i + 1] - xyz[0])) for i in range(1, len(xyz) - 1)
            )
            c = xyz.mean(axis=0)
            n += 1
            inside += int(np.hypot(c[0] - centre[0], c[1] - centre[1]) < radius)
    return n, area, inside


@pytest.mark.parametrize("body", ["text", "binary"])
def test_an_adapy_boolean_hole_reads_in_genie_as_a_hole(genie93, body, tmp_path):
    """Krande/adapy#410: a Plate with a PrimCyl/PrimBox cut, written by adapy, is a holed plate in GeniE.

    The round one is GeniE's plate_with_hole twin made in adapy (4 x 3 m, r 0.4 m at (2, 1.5)):
    GeniE meshes it as it meshes its own -- measured 209 elements, 11.52423605 m2 at 0.25 m, none in
    the hole -- though not node for node (GeniE's hole is a NURBS circle, adapy's an exact one).
    """
    square = ada.Plate("Sq", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01, origin=(0, 0, 10))
    square.add_boolean(ada.PrimBox("hole", (1.5, 1.0, 9.5), (2.5, 2.0, 10.5)))
    round_ = ada.Plate("Pl1", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01)
    round_.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
    gnx = (ada.Assembly("A") / (ada.Part("P") / [square, round_])).to_gnx(
        tmp_path / "holes.gnx", binary_acis=body == "binary"
    )
    read, holes = _genie_plates(gnx, tmp_path / "genie")
    assert {name: kind_faces for name, (*kind_faces, _area) in read.items()} == {
        "Sq": ["flat_plate", 1],
        "Pl1": ["flat_plate", 1],
    }
    assert read["Sq"][2] == "11"

    genie_mesh = _genie_mesh(genie93 / "plate_with_hole_text.gnx", tmp_path / "mesh_genie")
    alone = ada.Assembly("A") / (ada.Part("P") / round_)
    mesh_round = _genie_mesh(alone.to_gnx(tmp_path / "round.gnx", binary_acis=body == "binary"), tmp_path / "mesh_r")
    n_a, area_a, in_a = _mesh_stats(mesh_round, tmp_path / "a", (2, 1.5), 0.4)
    n_g, area_g, in_g = _mesh_stats(genie_mesh, tmp_path / "g", (2, 1.5), 0.4)
    assert (n_a, in_a) == (n_g, in_g) == (209, 0)
    assert area_a == pytest.approx(area_g, rel=1e-8)

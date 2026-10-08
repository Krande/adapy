"""Regenerate the Abaqus results-SQLite fixtures in ``files/fem_files/abaqus/results/``.

Each fixture is a deliberately tiny model, solved with Abaqus and exported with adapy's own
``abaqus-python`` ODB exporter, so the tests read a real export of a real ODB without an Abaqus
install. Needs Abaqus 2024+ on PATH (or ``ADA_ABAQUS_EXE``) and a license; each job is a few
seconds.

    python scripts/codegen/gen_abaqus_results_fixtures.py [names...]

    beam   10 x B31 cantilever: a static step (tip load; U, UR, RF, SF, S at four section points,
           tip-node U2 history) then a frequency step (4 modes; U, UR, EIGFREQ / EIGVAL history)
    shell  4 x 4 S4R plate under pressure, clamped along one edge (U; S at two section points)
    solid  1 x 1 x 4 C3D8 column, tip load (U; S at 8 integration points; E element-nodal)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "files" / "fem_files" / "abaqus" / "results"
sys.path.insert(0, str(ROOT / "src"))


def _grid_nodes(nx: int, ny: int, dx: float, dy: float) -> str:
    rows, n = [], 1
    for j in range(ny + 1):
        for i in range(nx + 1):
            rows.append(f"{n}, {i * dx:.6g}, {j * dy:.6g}, 0.")
            n += 1
    return "\n".join(rows)


def _beam() -> str:
    nodes = "\n".join(f"{i + 1}, {float(i):.1f}, 0., 0." for i in range(11))
    elems = "\n".join(f"{i + 1}, {i + 1}, {i + 2}" for i in range(10))
    return f"""*HEADING
adapy fixture: B31 cantilever, static + frequency
*NODE
{nodes}
*ELEMENT, TYPE=B31, ELSET=BEAM
{elems}
*BEAM SECTION, ELSET=BEAM, MATERIAL=STEEL, SECTION=RECT
0.1, 0.2
0., 0., -1.
*MATERIAL, NAME=STEEL
*ELASTIC
2.1E11, 0.3
*DENSITY
7850.,
*NSET, NSET=FIX
1
*NSET, NSET=TIP
11
*BOUNDARY
FIX, ENCASTRE
*STEP, NAME=static
*STATIC
*CLOAD
TIP, 2, -1000.
*OUTPUT, FIELD
*NODE OUTPUT
U, UR, RF
*ELEMENT OUTPUT
S, SF
*OUTPUT, HISTORY
*NODE OUTPUT, NSET=TIP
U2
*END STEP
*STEP, NAME=modes, PERTURBATION
*FREQUENCY, EIGENSOLVER=LANCZOS
4,
*OUTPUT, FIELD
*NODE OUTPUT
U, UR
*END STEP
"""


def _shell() -> str:
    n = 4
    elems = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i + 1
            elems.append(f"{j * n + i + 1}, {a}, {a + 1}, {a + n + 2}, {a + n + 1}")
    clamped = ", ".join(str(j * (n + 1) + 1) for j in range(n + 1))
    return f"""*HEADING
adapy fixture: S4R plate under pressure
*NODE
{_grid_nodes(n, n, 0.25, 0.25)}
*ELEMENT, TYPE=S4R, ELSET=PLATE
{chr(10).join(elems)}
*SHELL SECTION, ELSET=PLATE, MATERIAL=STEEL
0.01, 5
*MATERIAL, NAME=STEEL
*ELASTIC
2.1E11, 0.3
*NSET, NSET=CLAMPED
{clamped}
*BOUNDARY
CLAMPED, ENCASTRE
*STEP, NAME=pressure
*STATIC
*DLOAD
PLATE, P, 1000.
*OUTPUT, FIELD
*NODE OUTPUT
U
*ELEMENT OUTPUT
S
*END STEP
"""


def _solid() -> str:
    nodes, n = [], 1
    for k in range(5):
        for y in (0.0, 1.0):
            for x in (0.0, 1.0):
                nodes.append(f"{n}, {x:.1f}, {y:.1f}, {float(k):.1f}")
                n += 1
    elems = []
    for k in range(4):
        b = 4 * k
        # bottom face 1-2-4-3 counter-clockwise, then the top face
        elems.append(f"{k + 1}, {b + 1}, {b + 2}, {b + 4}, {b + 3}, {b + 5}, {b + 6}, {b + 8}, {b + 7}")
    return f"""*HEADING
adapy fixture: C3D8 column
*NODE
{chr(10).join(nodes)}
*ELEMENT, TYPE=C3D8, ELSET=COLUMN
{chr(10).join(elems)}
*SOLID SECTION, ELSET=COLUMN, MATERIAL=STEEL
*MATERIAL, NAME=STEEL
*ELASTIC
2.1E11, 0.3
*NSET, NSET=BASE
1, 2, 3, 4
*NSET, NSET=TOP
17, 18, 19, 20
*BOUNDARY
BASE, ENCASTRE
*STEP, NAME=load
*STATIC
*CLOAD
TOP, 1, 1000.
*OUTPUT, FIELD
*NODE OUTPUT
U
*ELEMENT OUTPUT
S
*ELEMENT OUTPUT, POSITION=NODES
E
*END STEP
"""


MODELS = {"beam": _beam, "shell": _shell, "solid": _solid}


def generate(name: str, workdir: pathlib.Path) -> pathlib.Path:
    from ada.fem.formats.abaqus.results.read_odb import (
        _abaqus_exe,
        convert_odb_to_sqlite,
    )

    exe = _abaqus_exe()
    if exe is None:
        raise SystemExit("Abaqus not found (on PATH as abaqus, or set ADA_ABAQUS_EXE)")
    (workdir / f"{name}.inp").write_text(MODELS[name](), encoding="ascii")
    res = subprocess.run([exe, f"job={name}", "interactive", "cpus=1"], cwd=workdir, capture_output=True, text=True)
    odb = workdir / f"{name}.odb"
    if res.returncode != 0 or not odb.exists():
        raise SystemExit(f"Abaqus job {name} failed:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
    db = convert_odb_to_sqlite(odb, workdir / f"{name}.sqlite", overwrite=True, exporter="abaqus-python")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{name}.sqlite"
    shutil.copyfile(db, out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help=f"fixtures to make (default: all of {', '.join(MODELS)})")
    parser.add_argument("--keep", type=pathlib.Path, help="keep the Abaqus job files in this directory")
    args = parser.parse_args(argv)
    names = args.names or list(MODELS)
    unknown = set(names) - set(MODELS)
    if unknown:
        parser.error(f"unknown fixture(s): {', '.join(sorted(unknown))}")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = args.keep or pathlib.Path(tmp)
        workdir.mkdir(parents=True, exist_ok=True)
        for name in names:
            out = generate(name, workdir)
            print(f"wrote {out.relative_to(ROOT)} ({os.path.getsize(out)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

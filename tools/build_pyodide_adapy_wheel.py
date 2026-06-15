#!/usr/bin/env python3
"""Build the pure-python ``ada-py`` wheel for pyodide/WASM.

The browser FEM/SAT conversion stacks ``micropip.install`` this wheel. It
is the normal ada-py source tree EXCEPT ``ada/__init__.py`` is swapped for
the pyodide-safe slim init (``deploy/pyodide-ada-init.py``) — so
``import ada`` and the WASM-viable subpackages (``ada.fem.results``,
``ada.cadit.sat``, ``ada.cad``, ``ada.occ.tessellating``) load under
emscripten without the native CAD deps (pythonocc-core / gmsh /
ifcopenshell). The slim init resolves the kernel-free top-level
re-exports lazily via PEP 562 ``__getattr__``.

Hand-builds the wheel zip with the standard library only — no
setuptools/build needed, so it runs in any environment (CI, the Docker
viewer build, a bare checkout). Install it with ``micropip.install(...,
deps=False)`` and provide numpy/h5py (loadPackage) + trimesh/pyquaternion
(micropip) yourself; the wheel's metadata intentionally declares no deps.

Usage:  python tools/build_pyodide_adapy_wheel.py [OUTDIR]
Prints the built wheel path on stdout.
"""

from __future__ import annotations

import base64
import hashlib
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC_ADA = REPO / "src" / "ada"
SLIM_INIT = REPO / "deploy" / "pyodide-ada-init.py"

DIST_NAME = "ada-py"
WHEEL_NAME = "ada_py"  # PEP 427 normalized (ada-py -> ada_py)
VERSION = "0.22.0"  # keep in sync with pyproject.toml [project].version

# Exclude compiled artefacts, caches, and committed test-run scratch
# (ada/fem/temp/ holds multi-MB eigen .frd/.rmed fixtures that must not
# bloat the browser download); ship everything else (source + resource
# files like ProfileDB.json) so runtime resource loads resolve.
_SKIP_DIRS = {"__pycache__", "temp"}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".pyd"}
# Bulky data/result fixtures that may sit alongside source but are never
# imported — dropping them keeps the wheel small without affecting any
# import or runtime resource load on the FEM/SAT/CAD paths.
_SKIP_DATA_SUFFIXES = {
    ".frd",
    ".rmed",
    ".med",
    ".vtu",
    ".xdmf",
    ".h5",
    ".odb",
    ".stp",
    ".step",
    ".ifc",
    ".sat",
    ".sif",
    ".sin",
}


def _iter_files():
    for p in sorted(SRC_ADA.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        suffix = p.suffix.lower()
        if suffix in _SKIP_SUFFIXES or suffix in _SKIP_DATA_SUFFIXES:
            continue
        yield p


def _record_line(arcname: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{arcname},sha256={digest},{len(data)}"


def build(outdir: Path) -> Path:
    if not SRC_ADA.is_dir():
        raise SystemExit(f"adapy package not found at {SRC_ADA}")
    if not SLIM_INIT.is_file():
        raise SystemExit(f"slim pyodide init not found at {SLIM_INIT}")

    outdir.mkdir(parents=True, exist_ok=True)
    distinfo = f"{WHEEL_NAME}-{VERSION}.dist-info"
    wheel_path = outdir / f"{WHEEL_NAME}-{VERSION}-py3-none-any.whl"

    records: list[str] = []
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in _iter_files():
            arc = "ada/" + str(p.relative_to(SRC_ADA)).replace("\\", "/")
            # Swap the eager real init for the pyodide-safe slim init.
            data = SLIM_INIT.read_bytes() if arc == "ada/__init__.py" else p.read_bytes()
            zf.writestr(arc, data)
            records.append(_record_line(arc, data))

        metadata = (
            "Metadata-Version: 2.1\n"
            f"Name: {DIST_NAME}\n"
            f"Version: {VERSION}\n"
            "Summary: adapy (pyodide/WASM pure-python build)\n"
            "Requires-Python: >=3.10\n"
        ).encode()
        wheelmeta = (
            "Wheel-Version: 1.0\n"
            "Generator: build_pyodide_adapy_wheel\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode()
        toplevel = b"ada\n"
        for fn, data in (
            (f"{distinfo}/METADATA", metadata),
            (f"{distinfo}/WHEEL", wheelmeta),
            (f"{distinfo}/top_level.txt", toplevel),
        ):
            zf.writestr(fn, data)
            records.append(_record_line(fn, data))

        record_name = f"{distinfo}/RECORD"
        records.append(f"{record_name},,")
        zf.writestr(record_name, ("\n".join(records) + "\n").encode())

    # Sidecar manifest so the browser worker resolves the wheel filename
    # without hardcoding the version (mirrors adacpp's /wheels/manifest.json,
    # kept separate so the two builds don't have to merge one file).
    import json

    (outdir / "adapy-manifest.json").write_text(json.dumps({"adapy": wheel_path.name}) + "\n")
    return wheel_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "dist_pyodide"
    print(build(out))

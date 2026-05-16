"""Regenerate `.SIN` test fixtures from their sibling `.SIF` files.

Used by maintainers when ``files/fem_files/cantilever/sesam/**.SIN``
needs refreshing — adapy itself never imports ``dnv-sifio``, but the
test suite does need stable SIN bytes to validate the pure-Python
``read_sin.py`` reader against.

The dnv-sifio path drags in pythonnet + ~30 MB of .NET runtime
binaries, which is exactly why we don't ship it as a runtime dep.
Use a throw-away environment instead, e.g.::

    pixi init scratch && cd scratch
    pixi add python=3.12 pip
    pixi run pip install dnv-sifio dnv-net-runtime
    pixi run python ../adapy/scripts/regen_sin_fixtures.py

The script walks every ``.SIF`` under ``files/fem_files/`` and writes
a sibling ``.SIN`` next to it. Existing SINs are overwritten only when
the SIF is newer (idempotent for unchanged inputs).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Default to the cantilever subtree — keeps fixture regen fast.
_DEFAULT_TREE = Path(__file__).resolve().parent.parent / "files" / "fem_files"

# Per-type field count helpers — dnv-sifio's writer needs the tab
# dimensions established before WriteData. The reader exposes them
# directly via GetTabDimensions; we just propagate those.
_TYPES_TO_COPY = [
    # Mesh
    "GNODE", "GCOORD", "GELMNT1", "GELREF1",
    # Sections / materials
    "GBEAMG", "GBOX", "GIORH", "GUSYI", "GELTH", "GPIPE",
    "MISOSEL", "MORSMEL",
    # BCs / loads
    "BNBCD", "BLDEP", "BEUSLO", "BNDOF",
    # Result definitions
    "RDPOINTS", "RDSTRESS", "RDIELCOR", "RDFORCES", "RDRESREF",
    "RDSERIES", "RDTRANS",
    # Result values
    "RVNODDIS", "RVNODVEL", "RVNODACC", "RVNODREA",
    "RVSTRESS", "RVSTRAIN", "RVFORCES",
    # Text records
    "TDNODE", "TDELEM", "TDMATER", "TDSECT", "TDRESREF", "TDSUPNAM",
]


def _copy_sif_to_sin(sif_path: Path, sin_path: Path) -> int:
    """Use dnv-sifio to read ``sif_path`` and write a sibling ``.SIN``.

    Returns the number of records written.
    """
    # Local imports — this module is only run from a scratch env that
    # has the package installed; importing at module load would crash
    # the import path for adapy itself.
    import dnv.net.runtime  # noqa: F401 — load .NET runtime side-effect
    from dnv.sesam.sifapi.io import SesamDataFactory
    from System import Array, Int64

    reader = SesamDataFactory.CreateReader(str(sif_path))
    diag = reader.CreateModel()
    if diag != 0:
        raise RuntimeError(f"{sif_path}: CreateModel returned {diag}")
    writer = SesamDataFactory.CreateWriter(str(sin_path))
    n_written = 0
    try:
        for type_name in _TYPES_TO_COPY:
            try:
                count = reader.GetCount(type_name)
            except Exception:
                continue
            if count == 0:
                continue
            dims = list(reader.GetTabDimensions(type_name))
            if dims:
                arr = Array[Int64](dims)
                writer.CreateTab(type_name, arr, len(dims))
            try:
                reader.SetFirstTimeReadAll(type_name)
                data = reader.ReadAll(type_name, 100_000)
                if not writer.WriteData(type_name, data):
                    print(f"  {type_name}: WriteData returned False")
                else:
                    n_written += data.Count
            except Exception as exc:
                print(f"  {type_name}: failed — {exc}")
    finally:
        writer.Close()
        reader.Close()
    return n_written


def main(tree: Path = _DEFAULT_TREE) -> None:
    sifs = sorted(tree.rglob("*.SIF"))
    if not sifs:
        print(f"No .SIF files under {tree}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Regenerating SIN fixtures from {len(sifs)} SIF source(s) under {tree}")
    for sif in sifs:
        sin = sif.with_suffix(".SIN")
        if sin.exists() and sin.stat().st_mtime >= sif.stat().st_mtime:
            print(f"  skip {sif.relative_to(tree)} (SIN up-to-date)")
            continue
        print(f"  gen  {sif.relative_to(tree)} -> {sin.name}")
        n = _copy_sif_to_sin(sif, sin)
        print(f"        {n} records, {sin.stat().st_size} bytes")


if __name__ == "__main__":
    tree = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _DEFAULT_TREE
    main(tree)

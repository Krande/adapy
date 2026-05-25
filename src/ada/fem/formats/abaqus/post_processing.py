"""Abaqus ODB → SQLite post-processing.

The legacy verification driver shelled out to an `ODBDump` binary to
convert `.odb` files into SQLite databases that adapy can query via
`SQLiteFEAStore`. The result wrapper (`FEAResultV2`) and the
post-processor (`post_processing_abaqus`) lived in the verification
report's helper module; they're not abaqus-specific in shape, but the
ODB-dump path absolutely is, so they belong here.

`ODB_DUMP_EXE` resolution order:
1. `ODBDump` on PATH (the typical container layout)
2. `ODB_DUMP_EXE` env var (override)
3. None — callers treat as "abaqus post-processing unavailable"
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Union

from ada.fem.formats.general import FEATypes
from ada.fem.results import EigenDataSummary
from ada.fem.results.sqlite_store import SQLiteFEAStore


def get_odb_dump_exe() -> Optional[pathlib.Path]:
    """Resolve the ODBDump exe path; None when unavailable."""
    found = shutil.which("ODBDump")
    if found is not None:
        return pathlib.Path(found)
    raw = os.getenv("ODB_DUMP_EXE")
    if raw is None:
        return None
    return pathlib.Path(raw)


@dataclass
class FEAResultV2:
    """SQLite-backed FEA result. Wraps an `.odb` + its dumped `.sqlite`.

    Mirrors `FEAResult`'s public shape (`name`, `software`,
    `results_file_path`, `get_eig_summary()`) but routes the result
    queries through `SQLiteFEAStore` instead of the format-specific
    parser. Used for abaqus today; could be reused by any solver
    whose results land in a SQLite DB.
    """

    name: str
    software: Union[str, FEATypes]
    results_db_path: Optional[pathlib.Path] = None
    results_file_path: Optional[pathlib.Path] = None

    def get_eig_summary(self) -> EigenDataSummary:
        """Read eigenfrequency + eigenvalue history out of the SQLite store."""
        from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

        fea_store = SQLiteFEAStore(self.results_db_path)
        results_freq = fea_store.get_history_data("EIGFREQ")
        results_val = fea_store.get_history_data("EIGVAL")
        modes = []
        for eig_freq, eig_val in zip(results_freq, results_val):
            step = eig_freq[-2]
            freq = eig_freq[-1]
            val = eig_val[-1]
            modes.append(EigenMode(int(step), f_hz=freq, eigenvalue=val))
        if not modes:
            raise ValueError(f"No eigenvalues found in the results for {self.name}")
        return EigenDataSummary(modes)


def post_processing_abaqus(
    odb_file: pathlib.Path, overwrite: bool = False
) -> FEAResultV2:
    """Dump an Abaqus `.odb` to SQLite, return a `FEAResultV2` over it.

    Wires into `AbaqusSetup.set_default_post_processor(...)` so adapy's
    standard `a.to_fem(...)` solver path produces a SQLite-queryable
    result for downstream reporting.
    """
    odb_dump_exe = get_odb_dump_exe()
    if odb_dump_exe is None:
        raise FileNotFoundError(
            "ODBDump executable not found on PATH or via ODB_DUMP_EXE env var"
        )
    sqlite_file = odb_file.with_suffix(".sqlite")
    if not sqlite_file.exists() or overwrite:
        proc = subprocess.run(
            [str(odb_dump_exe), "--odbFile", str(odb_file), "--sqliteFile", str(sqlite_file)],
            text=True,
            check=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ODBDump failed: {proc.stderr}")

    return FEAResultV2(
        name=sqlite_file.stem,
        software="abaqus",
        results_db_path=sqlite_file,
        results_file_path=odb_file,
    )

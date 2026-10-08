"""Abaqus ODB → results SQLite post-processing.

`post_processing_abaqus` exports an `.odb` to the results SQLite
(`ada/fem/results/resources/results.sql`, via `abaqus python`) and wraps it in an
`FEAResultV2`, which queries it through `SQLiteFEAStore` and materialises an `FEAResult` on demand.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Union

from ada.fem.formats.general import FEATypes
from ada.fem.results import EigenDataSummary
from ada.fem.results.sqlite_store import SQLiteFEAStore

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult


@dataclass
class FEAResultV2:
    """SQLite-backed FEA result. Wraps an `.odb` + its exported `.sqlite`.

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
    #: The solved model's beam sections (``ada.fem.results.line_sections``), grafted onto the mesh
    #: when it is materialised -- the dump itself has none.
    line_sections: Optional[object] = None

    @property
    def software_version(self) -> str:
        """The Abaqus release that wrote the result, from the ``.sta`` beside the ``.odb``."""
        from ada.fem.formats.abaqus.results.get_version_from_sta import (
            extract_abaqus_version,
        )

        if self.results_file_path is None:
            return "N/A"
        sta_file = pathlib.Path(self.results_file_path).with_suffix(".sta")
        return extract_abaqus_version(sta_file) if sta_file.exists() else "N/A"

    def get_eig_summary(self) -> EigenDataSummary:
        """The modal summary: from the ``.dat`` beside the ``.odb`` when there is one, else the SQLite store.

        The ``.dat`` has the participation factors and effective masses as well as the
        frequencies; the history output has only EIGFREQ / EIGVAL.
        """
        from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

        from_dat = _eigen_data_from_dat(self.results_file_path)
        if from_dat is not None:
            return from_dat

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

    def to_fea_result(self) -> FEAResult:
        """Materialise the SQLite as an in-memory :class:`FEAResult` (mesh + fields).

        What the rest of adapy consumes — the docs/artefact bake, the viewers — speaks
        `FEAResult`.
        """
        from ada.fem.formats.abaqus.results.read_odb import read_results_sqlite
        from ada.fem.results.line_sections import graft_line_sections

        result = read_results_sqlite(self.results_db_path, name=self.name, results_file_path=self.results_file_path)
        graft_line_sections(result.mesh, self.line_sections)
        return result


def _eigen_data_from_dat(results_file_path: Optional[pathlib.Path]) -> Optional[EigenDataSummary]:
    """The eigen tables of the ``.dat`` beside ``results_file_path``; None without one or without modes."""
    from ada.fem.formats.abaqus.results._results import get_eigen_data

    if results_file_path is None:
        return None
    dat_file = pathlib.Path(results_file_path).with_suffix(".dat")
    if not dat_file.is_file():
        return None
    summary = get_eigen_data(dat_file)
    return summary if summary.modes else None


def post_processing_abaqus(odb_file: pathlib.Path, overwrite: bool = False) -> FEAResultV2:
    """Export an Abaqus `.odb` to the results SQLite, return a `FEAResultV2` over it.

    Wires into `AbaqusSetup.set_default_post_processor(...)` so adapy's
    standard `a.to_fem(...)` solver path produces a SQLite-queryable
    result for downstream reporting.
    """
    from ada.fem.formats.abaqus.results.read_odb import convert_odb_to_sqlite

    odb_file = pathlib.Path(odb_file)
    sqlite_file = convert_odb_to_sqlite(odb_file, overwrite=overwrite)
    return FEAResultV2(
        name=sqlite_file.stem,
        software="abaqus",
        results_db_path=sqlite_file,
        results_file_path=odb_file,
    )

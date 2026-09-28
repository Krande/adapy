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
from typing import TYPE_CHECKING, Optional, Union

from ada.fem.formats.general import FEATypes
from ada.fem.results import EigenDataSummary
from ada.fem.results.sqlite_store import SQLiteFEAStore

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult


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

    def to_fea_result(self) -> FEAResult:
        """Materialise the SQLite dump as an in-memory :class:`FEAResult` (mesh + nodal fields).

        What the rest of adapy consumes — the docs/artefact bake, the viewers — speaks
        `FEAResult`, and the dump carries everything that needs: the instance's points and
        connectivity, and per-frame nodal U / UR as float32 blobs.
        """
        return read_odbdump_sqlite(self.results_db_path, name=self.name, results_file_path=self.results_file_path)


# Nodal field vars ODBDump writes one blob per component for; grouped back into one field each.
_NODAL_FIELD_GROUPS = {"U": ("U1", "U2", "U3"), "UR": ("UR1", "UR2", "UR3")}


def read_odbdump_sqlite(db_path: pathlib.Path, name: str = None, results_file_path: pathlib.Path = None) -> FEAResult:
    """Read an ODBDump SQLite file into an :class:`FEAResult`.

    Only the nodal U / UR fields are read (that's what ODBDump writes for the runs adapy makes).
    Result steps are numbered by frame, skipping each step's base-state frame 0, so a modal step's
    step numbers are its mode numbers, as with the other solvers' readers.
    """
    import sqlite3

    import numpy as np

    from ada.fem.formats.abaqus.elem_shapes import abaqus_el_type_to_ada
    from ada.fem.formats.abaqus.results.get_version_from_sta import (
        extract_abaqus_version,
    )
    from ada.fem.results.common import (
        ElementBlock,
        ElementInfo,
        FEAResult,
        FemNodes,
        Mesh,
    )
    from ada.fem.results.field_data import NodalFieldData, NodalFieldType

    db_path = pathlib.Path(db_path)
    conn = sqlite3.connect(db_path)
    try:
        instances = [r[0] for r in conn.execute("SELECT DISTINCT InstanceID FROM Points")]
        if len(instances) != 1:
            raise NotImplementedError(f"{db_path.name}: multi-instance results are not supported ({instances})")
        inst = instances[0]

        points = conn.execute("SELECT ID, X, Y, Z FROM Points WHERE InstanceID = ? ORDER BY ID", (inst,)).fetchall()
        node_ids = np.array([p[0] for p in points], dtype=int)
        nodes = FemNodes(coords=np.array([p[1:] for p in points], dtype=float), identifiers=node_ids)

        conn_rows = conn.execute(
            "SELECT ElemID, PointID FROM ElementConnectivity WHERE InstanceID = ? ORDER BY ElemID, Seq", (inst,)
        ).fetchall()
        elem_nodes: dict[int, list[int]] = {}
        for el_id, point_id in conn_rows:
            elem_nodes.setdefault(el_id, []).append(point_id)

        by_type: dict[str, list[int]] = {}
        for el_id, el_type in conn.execute(
            "SELECT ElemID, Type FROM ElementInfo WHERE InstanceID = ? ORDER BY ElemID", (inst,)
        ):
            by_type.setdefault(el_type, []).append(el_id)
        blocks = []
        for el_type, el_ids in by_type.items():
            elem_info = ElementInfo(
                type=abaqus_el_type_to_ada(el_type), source_software=FEATypes.ABAQUS, source_type=el_type
            )
            node_refs = np.array([elem_nodes[i] for i in el_ids], dtype=int)
            blocks.append(ElementBlock(elem_info=elem_info, node_refs=node_refs, identifiers=np.array(el_ids)))
        mesh = Mesh(elements=blocks, nodes=nodes)

        # Mode number -> (frequency, eigenvalue), from the history output of a modal step.
        eig = {}
        hist = conn.execute(
            """SELECT fv.Name, ho.Frame, ho.Value FROM HistOutput ho
               JOIN FieldVars fv ON fv.FieldID = ho.FieldVarID
               WHERE fv.Name IN ('EIGFREQ', 'EIGVAL')"""
        ).fetchall()
        for var, frame, value in hist:
            eig.setdefault(int(frame), {})[var] = value

        frames = conn.execute(
            "SELECT StepID, FrameID, FrameValue FROM Frames WHERE FrameID > 0 ORDER BY StepID, FrameID"
        ).fetchall()
        blobs = {
            (step_id, frame, var): data
            for step_id, frame, var, data in conn.execute(
                """SELECT fn.StepID, fn.Frame, fv.Name, fn.Data FROM FieldNodes fn
                   JOIN FieldVars fv ON fv.FieldID = fn.FieldVarID
                   WHERE fn.InstanceID = ? AND fn.IsImaginary = 0""",
                (inst,),
            )
        }
    finally:
        conn.close()

    is_modal = bool(eig)
    fields = []
    for result_step, (step_id, frame_id, frame_value) in enumerate(frames, start=1):
        if is_modal:
            result_step = frame_id
        mode = eig.get(frame_id, {}) if is_modal else {}
        for field_name, components in _NODAL_FIELD_GROUPS.items():
            cols = [blobs.get((step_id, frame_value, c)) for c in components]
            if any(c is None for c in cols):
                continue
            values = np.column_stack([node_ids] + [np.frombuffer(c, dtype="<f4").astype(float) for c in cols])
            fields.append(
                NodalFieldData(
                    field_name,
                    result_step,
                    list(components),
                    values,
                    eigen_freq=mode.get("EIGFREQ"),
                    eigen_value=mode.get("EIGVAL"),
                    field_type=NodalFieldType.DISP if field_name == "U" else NodalFieldType.UNKNOWN,
                )
            )

    software_version = "N/A"
    if results_file_path is not None:
        sta_file = pathlib.Path(results_file_path).with_suffix(".sta")
        if sta_file.exists():
            software_version = extract_abaqus_version(sta_file)

    return FEAResult(
        name=name or db_path.stem,
        software=FEATypes.ABAQUS,
        results=fields,
        mesh=mesh,
        results_file_path=results_file_path or db_path,
        software_version=software_version,
    )


def post_processing_abaqus(odb_file: pathlib.Path, overwrite: bool = False) -> FEAResultV2:
    """Dump an Abaqus `.odb` to SQLite, return a `FEAResultV2` over it.

    Wires into `AbaqusSetup.set_default_post_processor(...)` so adapy's
    standard `a.to_fem(...)` solver path produces a SQLite-queryable
    result for downstream reporting.
    """
    odb_dump_exe = get_odb_dump_exe()
    if odb_dump_exe is None:
        raise FileNotFoundError("ODBDump executable not found on PATH or via ODB_DUMP_EXE env var")
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

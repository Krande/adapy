"""Abaqus results: ``.odb`` -> results SQLite (``ada/fem/results/resources/results.sql``) -> FEAResult.

The ODB -> SQLite step is pluggable (:func:`register_odb_exporter`); whichever exporter runs, the
SQLite it writes is what every reader here (and the streaming bake, ``sqlite_stream``) reads.

Built-in exporters, in order of preference:

``command``
    Any executable that writes the schema, given as a command template in
    ``$ADA_ODB_EXPORT_CMD`` with ``{odb}`` and ``{sqlite}`` placeholders, e.g.
    ``my-exporter {odb} {sqlite}``. Available when the variable is set.
``abaqus-python``
    ``aba_io.py`` under ``abaqus python`` (Abaqus 2024+). Available when the Abaqus executable
    (``$ADA_ABAQUS_EXE``, default ``abaqus``) is on PATH.

``$ADA_ODB_EXPORTER`` (or the ``exporter`` argument) picks one by name; otherwise the first
available one runs. Packages can add exporters by calling :func:`register_odb_exporter`, or from an
``ada.odb_exporters`` entry point whose target is a no-argument function that does.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import shutil
import sqlite3
import subprocess
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import numpy as np

from ada.config import logger
from ada.fem.formats.abaqus.results.get_version_from_sta import extract_abaqus_version

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult, Mesh
    from ada.fem.results.field_data import FieldData

_script_dir = pathlib.Path(__file__).parent.resolve().absolute()

ABA_IO = _script_dir / "aba_io.py"

ODB_EXPORT_CMD_ENV = "ADA_ODB_EXPORT_CMD"
ODB_EXPORTER_ENV = "ADA_ODB_EXPORTER"


# ---------------------------------------------------------------------------
# ODB -> SQLite exporters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OdbExporter:
    """Writes the results SQLite for an ODB: ``export(odb_path, sqlite_path)``."""

    name: str
    export: Callable[[pathlib.Path, pathlib.Path], None]
    is_available: Callable[[], bool] = lambda: True


_ODB_EXPORTERS: dict[str, OdbExporter] = {}


def register_odb_exporter(
    name: str,
    export: Callable[[pathlib.Path, pathlib.Path], None],
    is_available: Callable[[], bool] = lambda: True,
    prefer: bool = False,
) -> OdbExporter:
    """Register (or replace) an ODB -> SQLite exporter. ``prefer`` puts it ahead of the others."""
    _load_odb_exporters()
    exporter = OdbExporter(name, export, is_available)
    _ODB_EXPORTERS.pop(name, None)
    if prefer:
        rest = dict(_ODB_EXPORTERS)
        _ODB_EXPORTERS.clear()
        _ODB_EXPORTERS[name] = exporter
        _ODB_EXPORTERS.update(rest)
    else:
        _ODB_EXPORTERS[name] = exporter
    return exporter


def odb_exporters() -> list[OdbExporter]:
    """Every registered exporter, in order of preference."""
    _load_odb_exporters()
    return list(_ODB_EXPORTERS.values())


def get_odb_exporter(name: str | None = None) -> OdbExporter:
    """The exporter named (argument, else ``$ADA_ODB_EXPORTER``), else the first available one."""
    exporters = {e.name: e for e in odb_exporters()}
    name = name or os.getenv(ODB_EXPORTER_ENV) or None
    if name is not None:
        if name not in exporters:
            raise KeyError(f'no ODB exporter "{name}"; registered: {sorted(exporters)}')
        return exporters[name]
    for exporter in exporters.values():
        if exporter.is_available():
            return exporter
    raise FileNotFoundError(
        "no ODB -> SQLite exporter available: put Abaqus on PATH (or set ADA_ABAQUS_EXE), set "
        f"{ODB_EXPORT_CMD_ENV}, or register one with register_odb_exporter"
    )


def odb_exporter_available() -> bool:
    try:
        get_odb_exporter()
    except (FileNotFoundError, KeyError):
        return False
    return True


def _command_export(odb_path: pathlib.Path, sqlite_path: pathlib.Path) -> None:
    template = os.environ[ODB_EXPORT_CMD_ENV]
    # Non-POSIX splitting keeps Windows backslashes, and the quotes around a token (stripped here).
    tokens = [t.strip('"') for t in shlex.split(template, posix=os.name != "nt")]
    cmd = [t.format(odb=str(odb_path), sqlite=str(sqlite_path)) for t in tokens]
    res = subprocess.run(cmd, cwd=sqlite_path.parent, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({res.returncode}):\n{res.stderr}")


def _abaqus_exe() -> str | None:
    return shutil.which(os.getenv("ADA_ABAQUS_EXE", "abaqus"))


def _abaqus_python_export(odb_path: pathlib.Path, sqlite_path: pathlib.Path) -> None:
    from ada.fem.results.sqlite_schema import SCHEMA_PATH

    exe = _abaqus_exe()
    if exe is None:
        raise FileNotFoundError('Abaqus executable not found (on PATH as "abaqus", or set ADA_ABAQUS_EXE)')
    cmd = [exe, "python", str(ABA_IO), str(odb_path), str(sqlite_path), str(SCHEMA_PATH)]
    res = subprocess.run(cmd, cwd=sqlite_path.parent, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Abaqus/Python export failed ({res.returncode}):\n{res.stderr}")


def _load_odb_exporters() -> None:
    if getattr(_load_odb_exporters, "_done", False):
        return
    _load_odb_exporters._done = True  # type: ignore[attr-defined]
    _ODB_EXPORTERS.setdefault(
        "command", OdbExporter("command", _command_export, lambda: bool(os.getenv(ODB_EXPORT_CMD_ENV)))
    )
    _ODB_EXPORTERS.setdefault(
        "abaqus-python", OdbExporter("abaqus-python", _abaqus_python_export, lambda: _abaqus_exe() is not None)
    )
    from importlib.metadata import entry_points

    for ep in entry_points(group="ada.odb_exporters"):
        try:
            ep.load()()
        except Exception as e:  # noqa: BLE001 - a broken plugin must not take the built-ins down
            logger.warning(f'ODB exporter plugin "{ep.name}" failed to load: {e}')


def convert_odb_to_sqlite(
    odb_path: str | os.PathLike,
    sqlite_path: str | os.PathLike | None = None,
    overwrite: bool = False,
    exporter: str | None = None,
) -> pathlib.Path:
    """Export an ``.odb`` to the results SQLite with an ODB exporter (see the module docstring).

    ``sqlite_path`` defaults to the ``.sqlite`` beside the ODB, which is reused while it is newer
    than the ODB (``overwrite`` forces a new export).
    """
    odb_path = pathlib.Path(odb_path).resolve()
    sqlite_path = odb_path.with_suffix(".sqlite") if sqlite_path is None else pathlib.Path(sqlite_path).resolve()
    if sqlite_path.exists() and not overwrite and sqlite_path.stat().st_mtime >= odb_path.stat().st_mtime:
        return sqlite_path

    chosen = get_odb_exporter(exporter)
    logger.info(f'Exporting "{odb_path.name}" to "{sqlite_path.name}" with the "{chosen.name}" ODB exporter')
    if sqlite_path.exists():
        sqlite_path.unlink()
    chosen.export(odb_path, sqlite_path)
    if not sqlite_path.exists():
        raise RuntimeError(f'the "{chosen.name}" ODB exporter wrote no {sqlite_path}')
    return sqlite_path


def read_odb(result_file_path: str | os.PathLike, overwrite: bool = False) -> FEAResult:
    """An Abaqus result as an :class:`FEAResult`: an ``.odb`` (exported first) or its results ``.sqlite``.

    The default Abaqus post-processor (``AbaqusSetup.default_post_processor``).
    """
    path = pathlib.Path(result_file_path)
    if path.suffix.lower() == ".odb":
        db = convert_odb_to_sqlite(path, overwrite=overwrite)
        return read_results_sqlite(db, name=path.stem, results_file_path=path)
    return read_results_sqlite(path)


# ---------------------------------------------------------------------------
# SQLite -> FEAResult
# ---------------------------------------------------------------------------


def _field_name(components: list[str], description: str) -> str:
    """``U1, U2, U3`` -> ``U``: the components' shared prefix, else the field description."""
    heads = {c.rstrip("0123456789") for c in components}
    if len(heads) == 1:
        head = heads.pop()
        if head:
            return head
    return description or components[0]


_NODAL_FIELD_TYPES = {"U": "DISP", "V": "VEL", "RF": "FORCE", "CF": "FORCE"}


def read_results_sqlite(
    db_path: str | os.PathLike, name: str = None, results_file_path: str | os.PathLike = None
) -> FEAResult:
    """Read a results SQLite into an :class:`FEAResult` (one model instance).

    Every nodal field, grouped back from its per-component rows, and the integration-point
    element fields. Where U and UR are both there, UR is read as U's rotational components, so U
    is ``U1..U3, UR1..UR3``, the way the other solvers' displacements carry theirs. Result steps
    are numbered by frame, skipping each step's base-state frame 0; when every step is modal the
    step numbers are the mode numbers, as with the other solvers' readers.
    """
    from ada.fem.formats.abaqus.elem_shapes import abaqus_el_type_to_ada
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import (
        ElementBlock,
        ElementInfo,
        FEAResult,
        FemNodes,
        Mesh,
    )
    from ada.fem.results.field_data import (
        ElementFieldData,
        FieldPosition,
        NodalFieldData,
        NodalFieldType,
    )
    from ada.fem.results.sqlite_schema import check_schema_version, order_components

    db_path = pathlib.Path(db_path)
    conn = sqlite3.connect(db_path)
    try:
        check_schema_version(conn, db_path.name)
        instances = [r[0] for r in conn.execute("SELECT DISTINCT InstanceID FROM Points ORDER BY InstanceID")]
        if len(instances) != 1:
            raise NotImplementedError(f"{db_path.name}: multi-instance results are not supported ({instances})")
        inst = instances[0]

        points = conn.execute("SELECT ID, X, Y, Z FROM Points WHERE InstanceID = ? ORDER BY ID", (inst,)).fetchall()
        node_ids = np.array([p[0] for p in points], dtype=int)
        nodes = FemNodes(coords=np.array([p[1:] for p in points], dtype=float), identifiers=node_ids)

        elem_nodes: dict[int, list[int]] = {}
        for el_id, point_id in conn.execute(
            "SELECT ElemID, PointID FROM ElementConnectivity WHERE InstanceID = ? ORDER BY ElemID, Seq", (inst,)
        ):
            elem_nodes.setdefault(el_id, []).append(point_id)
        # Within a type, element order is the element blobs' order.
        by_type: dict[str, list[int]] = {}
        for el_id, el_type in conn.execute(
            "SELECT ElemID, Type FROM ElementInfo WHERE InstanceID = ? ORDER BY Type, ElemID", (inst,)
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

        var_rows = conn.execute("SELECT FieldID, Name, Description FROM FieldVars ORDER BY FieldID").fetchall()
        var_name = {fid: n for fid, n, _ in var_rows}

        # (step, mode) -> {EIGFREQ: .., EIGVAL: ..}, from the history output of the modal steps.
        eig: dict[tuple[int, int], dict[str, float]] = {}
        for var, step_id, frame, value in conn.execute(
            """SELECT fv.Name, ho.StepID, ho.Frame, ho.Value FROM HistOutput ho
               JOIN FieldVars fv ON fv.FieldID = ho.FieldVarID WHERE fv.Name IN ('EIGFREQ', 'EIGVAL')"""
        ):
            eig.setdefault((int(step_id), int(frame)), {})[var] = value

        steps = {sid: (dom or "") for sid, dom in conn.execute("SELECT ID, DomainType FROM Steps")}
        frames = conn.execute(
            "SELECT StepID, FrameID, FrameValue FROM Frames WHERE FrameID > 0 ORDER BY StepID, FrameID"
        ).fetchall()
        nodal_blobs = {
            (step_id, frame, var_id): data
            for step_id, frame, var_id, data in conn.execute(
                "SELECT StepID, Frame, FieldVarID, Data FROM FieldNodes WHERE InstanceID = ? AND IsImaginary = 0",
                (inst,),
            )
        }
        elem_blobs: dict[tuple[int, float, str], dict[int, tuple[int, bytes]]] = {}
        for step_id, frame, var_id, el_type, n_ips, data in conn.execute(
            """SELECT StepID, Frame, FieldVarID, ElemType, NIPs, Data FROM FieldElem
               WHERE InstanceID = ? AND IsImaginary = 0 AND Location = 'INTEGRATION_POINT'""",
            (inst,),
        ):
            elem_blobs.setdefault((step_id, frame, el_type), {})[var_id] = (int(n_ips or 1), data)
    finally:
        conn.close()

    # Fields: components grouped by their field's description, in component order.
    by_desc: dict[str, list[tuple[int, str]]] = {}
    for fid, var, desc in var_rows:
        by_desc.setdefault(desc or "", []).append((fid, var))
    groups = {desc: [fid for fid, _ in order_components(members)] for desc, members in by_desc.items()}
    nodal_vars = {fid for (_, _, fid) in nodal_blobs}
    elem_vars = {fid for per in elem_blobs.values() for fid in per}

    all_modal = bool(steps) and all(dom.upper() == "MODAL" for dom in steps.values())
    fields: list[FieldData] = []
    for seq, (step_id, frame_id, frame_value) in enumerate(frames, start=1):
        result_step = frame_id if all_modal else seq
        mode = eig.get((step_id, frame_id), {})

        nodal: dict[str, tuple[list[str], list[np.ndarray]]] = {}
        for desc, fids in groups.items():
            fids = [f for f in fids if f in nodal_vars]
            cols = [nodal_blobs.get((step_id, frame_value, f)) for f in fids]
            if not fids or any(c is None for c in cols):
                continue
            comps = [var_name[f] for f in fids]
            nodal[_field_name(comps, desc)] = (comps, [np.frombuffer(c, dtype="<f4").astype(float) for c in cols])
        # Kept apart, UR was a field of unknown type that the bake dropped with every
        # non-displacement field, so a beam model had no rotations to draw a torsion mode with.
        if "U" in nodal and "UR" in nodal:
            nodal["U"] = (nodal["U"][0] + nodal["UR"][0], nodal["U"][1] + nodal["UR"][1])
            del nodal["UR"]
        for field_name, (comps, columns) in nodal.items():
            fields.append(
                NodalFieldData(
                    field_name,
                    result_step,
                    comps,
                    np.column_stack([node_ids] + columns),
                    eigen_freq=mode.get("EIGFREQ"),
                    eigen_value=mode.get("EIGVAL"),
                    field_type=NodalFieldType[_NODAL_FIELD_TYPES.get(field_name, "UNKNOWN")],
                )
            )

        for el_type, el_ids in by_type.items():
            per_var = elem_blobs.get((step_id, frame_value, el_type))
            if not per_var:
                continue
            for desc, fids in groups.items():
                fids = [f for f in fids if f in elem_vars and f in per_var]
                if not fids:
                    continue
                n_ips = per_var[fids[0]][0]
                cols = [np.frombuffer(per_var[f][1], dtype="<f4").astype(float) for f in fids]
                labels = np.repeat(np.asarray(el_ids, dtype=float), n_ips)
                ips = np.tile(np.arange(1, n_ips + 1, dtype=float), len(el_ids))
                comps = [var_name[f] for f in fids]
                fields.append(
                    ElementFieldData(
                        _field_name(comps, desc),
                        result_step,
                        comps,
                        np.column_stack([labels, ips] + cols),
                        eigen_freq=mode.get("EIGFREQ"),
                        eigen_value=mode.get("EIGVAL"),
                        field_pos=FieldPosition.INT,
                        elem_type=abaqus_el_type_to_ada(el_type),
                    )
                )

    software_version = "N/A"
    if results_file_path is not None:
        sta_file = pathlib.Path(results_file_path).with_suffix(".sta")
        if sta_file.exists():
            software_version = extract_abaqus_version(sta_file)

    eigen_mode_data = None
    if eig and results_file_path is not None:
        from ada.fem.formats.abaqus.post_processing import _eigen_data_from_dat

        eigen_mode_data = _eigen_data_from_dat(pathlib.Path(results_file_path))

    return FEAResult(
        name=name or db_path.stem,
        software=FEATypes.ABAQUS,
        results=fields,
        mesh=mesh,
        results_file_path=pathlib.Path(results_file_path) if results_file_path is not None else db_path,
        software_version=software_version,
        eigen_mode_data=eigen_mode_data,
    )


def read_history(db_path: str | os.PathLike) -> list[tuple]:
    """Every history sample: ``(step name, step procedure, region, output name, time, value)``."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            """SELECT st.Name, st.Procedure, ho.Region, fv.Name, ho.Frame, ho.Value
               FROM HistOutput ho
               JOIN FieldVars fv ON fv.FieldID = ho.FieldVarID
               JOIN Steps st ON st.ID = ho.StepID
               ORDER BY ho.StepID, ho.Region, ho.FieldVarID, ho.Frame"""
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Deprecated: the pickle export (pre-SQLite)
# ---------------------------------------------------------------------------


def read_odb_pckle_file(result_file_path: str | pathlib.Path, overwrite=False) -> FEAResult:
    """Deprecated: read a ``.pckle`` written by adapy's former pickle exporter.

    Only an existing ``.pckle`` is read; an ``.odb`` goes through :func:`read_odb`. Unpickling
    runs code from the file, so read only files you trust.
    """
    import pickle

    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import FEAResult

    warnings.warn(
        "read_odb_pckle_file is deprecated; use read_odb, which reads the .odb through the results SQLite",
        DeprecationWarning,
        stacklevel=2,
    )
    result_file_path = pathlib.Path(result_file_path)
    if result_file_path.suffix.lower() == ".odb":
        return read_odb(result_file_path, overwrite=overwrite)

    with open(result_file_path, "rb") as f:
        data = pickle.load(f)

    mesh = get_odb_instance_data(data["rootAssembly"]["instances"])
    fields = get_odb_frame_data(data["steps"])

    software_version = "N/A"
    sta_file = result_file_path.with_suffix(".sta")
    if sta_file.exists():
        software_version = extract_abaqus_version(sta_file)

    return FEAResult(
        name=result_file_path.stem,
        software=FEATypes.ABAQUS,
        mesh=mesh,
        results=fields,
        results_file_path=result_file_path,
        software_version=software_version,
    )


def get_odb_field_data(field_name, field_data, frame_num):
    from ada.fem.results.field_data import (
        ElementFieldData,
        NodalFieldData,
        NodalFieldType,
    )

    field_type, components, data = field_data

    if field_type == "ELEMENT_NODAL":
        field_values = np.array(list(yield_elem_nodal_data(data)))
        return ElementFieldData(field_name, frame_num, components, values=field_values)
    elif field_type == "NODAL":
        field_values = np.array(list(yield_nodal_data(data)))
        if field_name == "U":
            field_type_general = NodalFieldType.DISP
        elif field_name == "V":
            field_type_general = NodalFieldType.VEL
        elif field_name == "F":
            field_type_general = NodalFieldType.FORCE
        else:
            field_type_general = NodalFieldType.UNKNOWN

        return NodalFieldData(field_name, frame_num, components, field_values, field_type=field_type_general)
    else:
        raise NotImplementedError()


def get_odb_frame_data(steps: dict) -> list[FieldData]:
    frame_num = 0
    fields = []
    for step_name, step_data in dict(sorted(steps.items(), key=lambda x: x[1]["totalTime"])).items():
        for frame in step_data["frames"]:
            for key, value in frame.items():
                field = get_odb_field_data(key, value["values"], frame_num)
                fields.append(field)
            frame_num += 1

    return fields


def get_odb_instance_data(instances) -> Mesh:
    from ada.fem.formats.abaqus.elem_shapes import abaqus_el_type_to_ada
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import ElementBlock, ElementInfo, FemNodes, Mesh

    if len(instances) > 1:
        raise NotImplementedError("Multi-instances results are not yet supported")

    instance = instances[0]

    ids, coords = zip(*instance["nodes"])
    el_ids, el_type_array, nodes_connectivity, sec_cat = zip(*instance["elements"])
    el_type_set = set(el_type_array)
    if len(el_type_set) != 1:
        raise NotImplementedError("Mixed element sets not yet supported")

    el_type = el_type_array[0]
    shape = abaqus_el_type_to_ada(el_type)
    elem_info = ElementInfo(type=shape, source_software=FEATypes.ABAQUS, source_type=el_type)
    el_block = ElementBlock(
        elem_info=elem_info, node_refs=np.array(nodes_connectivity, dtype=int), identifiers=np.array(el_ids, dtype=int)
    )
    el_blocks = [el_block]
    nodes = FemNodes(coords=np.array(coords, dtype=float), identifiers=np.array(ids, dtype=int))

    return Mesh(elements=el_blocks, nodes=nodes)


def yield_elem_nodal_data(data):
    for x in data:
        spn = x["sec_p_num"]
        if isinstance(spn, dict):
            spn = -1
        if isinstance(x["data"], list) is False:
            x["data"] = [x["data"]]

        yield x["elementLabel"], spn, x["nodeLabel"], *x["data"]


def yield_nodal_data(data):
    for x in data:
        yield x[0], *x[1]

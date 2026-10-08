"""Streaming reader for the FEA results SQLite (``ada/fem/results/resources/results.sql``).

Serves an exported Abaqus result through the :class:`FEAStreamReader` protocol, so the artefact
bake flows it through the same path as RMED and SIF. Opt-in: :func:`register` adds the
``.sqlite`` (and, with Abaqus available, ``.odb``) factories to the stream-reader registry.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from typing import Iterator

import numpy as np

from ada.fem.results.artefacts import (
    ElementFieldSpec,
    ElementStepValues,
    FieldCategory,
    FieldSpec,
    HistoryRecords,
    HistoryRegion,
    HistorySeries,
    HistoryStep,
    HistoryVariable,
    MeshGeometry,
    StepValues,
)
from ada.fem.results.common import CellBlockData
from ada.fem.results.sqlite_schema import (
    check_schema_version,
    is_results_db,
    order_components,
)

# Abaqus element-type code → meshio cell_type string. Mirrors the
# adapy ``ada_to_abaqus_format`` map (src/ada/fem/formats/abaqus/
# elem_shapes.py) but inverted and lowercased so the values match
# what ``CellBlockData.cell_type`` consumers expect.
_ABAQUS_TYPE_TO_MESHIO: dict[str, str] = {
    "B31": "line",
    "B31H": "line",
    "B32": "line3",
    # Connector elements are 2-node by construction (CONN3D2 / CONN2D2);
    # the kinematic constraints sit on top but the geometry the bake
    # cares about is just a line. Spring / dashpot 2-node elements
    # share the same "two points + a constitutive law" shape.
    "CONN3D2": "line",
    "CONN2D2": "line",
    "SPRING1": "line",
    "SPRING2": "line",
    "SPRINGA": "line",
    "DASHPOT1": "line",
    "DASHPOT2": "line",
    "DASHPOTA": "line",
    # Lumped / point-shaped elements — single node, render as a
    # vertex. Mass and rotary inertia are zero-volume; we still emit
    # a cell so the (n_elements, n_ips, ...) field arrays line up.
    # Point elements (1 node, zero volume). meshio's ``vertex`` cell
    # type fits but adapy's bake has no rendering / writer for it, so
    # ``_SKIP_RENDER_TYPES`` below filters them out before they reach
    # cell_blocks / element_field_specs. Their FieldElem rows stay
    # queryable from the sqlite — they just don't surface through
    # the streaming protocol today.
    "MASS": "vertex",
    "ROTARYI": "vertex",
    "S3": "triangle",
    "S3R": "triangle",
    "S3RS": "triangle",
    "R3D3": "triangle",
    "STRI65": "triangle6",
    "S4": "quad",
    "S4R": "quad",
    "R3D4": "quad",
    "S8": "quad8",
    "S8R": "quad8",
    "C3D4": "tetra",
    "C3D10": "tetra10",
    "C3D8": "hexahedron",
    "C3D8R": "hexahedron",
    "C3D8H": "hexahedron",
    "C3D20": "hexahedron20",
    "C3D20R": "hexahedron20",
    "C3D20RH": "hexahedron20",
    "C3D6": "wedge",
    "C3D15": "wedge15",
    "C3D5": "pyramid",
    "C3D5H": "pyramid",
}

# Element types we deliberately hide from the streaming reader's
# protocol surface even though we map them above. Their FieldElem
# rows still land in the sqlite and stay readable for any caller
# that bypasses the protocol (e.g. a future history-table view).
_SKIP_RENDER_TYPES: frozenset[str] = frozenset({"MASS", "ROTARYI"})


def _location_to_support(location: str) -> str:
    """Map an Abaqus result-position string to adapy's
    ``ElementFieldSpec.support`` enum.

    The exporter records the position verbatim from the Abaqus
    field-output API; the protocol
    surface only distinguishes ``gauss`` (per-IP) from
    ``element_nodal`` (per-corner extrapolation), so CENTROID + WHOLE
    + UNDEFINED-shaped buckets collapse into ``gauss`` (single-IP).
    """

    low = (location or "").upper()
    if low == "ELEMENT_NODAL":
        return "element_nodal"
    return "gauss"


def _classify_abaqus_field(description: str, components: list[str]) -> FieldCategory:
    """Map an Abaqus field to adapy's coarse FieldCategory.

    Matches on the Description string first (it carries the most
    information — "Spatial displacement", "Stress components", etc.),
    then falls back to the component-name prefix ("U1", "S11", "RF2",
    "E11"). Conservative: anything we don't recognise lands in
    ``other`` so the viewer treats it as a passive scalar/vector.
    """

    desc = (description or "").lower()
    if "displacement" in desc or "translation" in desc:
        return "displacement"
    if "reaction" in desc:
        return "reaction"
    if "stress" in desc or "mises" in desc:
        return "stress"
    if "strain" in desc:
        return "strain"

    first = components[0] if components else ""
    head = first.rstrip("0123456789")
    if head == "U":
        return "displacement"
    if head == "RF":
        return "reaction"
    if head in ("S", "PRESS", "MISES"):
        return "stress"
    if head in ("E", "EE", "PE", "LE", "NE", "PEEQ"):
        return "strain"
    return "other"


def _classify_history_region(
    res_type: str | None,
    instance_id: int,
    point_id: int,
    elem_id: int,
    region_name: str | None,
) -> tuple[str, str]:
    """Classify an HistOutput row's region into (kind, region_id).

    Abaqus stores ``ResType`` as a position enum string (``NODAL``,
    ``INTEGRATION_POINT``, ``WHOLE_ELEMENT``, ``WHOLE_MODEL``, etc.).
    We collapse those into the manifest's coarser kind set:

    * ``node``     — NODAL / nodal output at a labelled node
    * ``element``  — anything attached to an element label
    * ``model``    — assembly-wide output (both labels are -1)
    * ``set``      — fallback for set-scoped history (rare)

    ``region_id`` is the stable key the series rows refer back to.
    """

    rt = (res_type or "").upper()
    has_node = point_id is not None and int(point_id) > 0
    has_elem = elem_id is not None and int(elem_id) > 0

    if rt == "NODAL" or has_node and not has_elem:
        kind = "node"
        ident = f"inst{int(instance_id)}:node:{int(point_id)}"
    elif has_elem:
        kind = "element"
        ident = f"inst{int(instance_id)}:elem:{int(elem_id)}"
    elif rt in ("WHOLE_MODEL", "WHOLE_REGION") or not (has_node or has_elem):
        kind = "model"
        ident = f"inst{int(instance_id)}:model"
    else:
        kind = "set"
        ident = f"inst{int(instance_id)}:set:{region_name or ''}"

    if region_name:
        ident = f"{ident}:{region_name}"
    return kind, ident


def _format_region_display(
    kind: str,
    instance_name: str | None,
    point_id: int,
    elem_id: int,
    region_name: str | None,
) -> str:
    inst = instance_name or ""
    if kind == "node":
        return f"{inst}: Node {int(point_id)}" if inst else f"Node {int(point_id)}"
    if kind == "element":
        return f"{inst}: Elem {int(elem_id)}" if inst else f"Elem {int(elem_id)}"
    if kind == "model":
        return inst or "ASSEMBLY"
    return region_name or inst or "region"


# Abaqus history-variable conventions:
# - U1/U2/U3, V1/V2/V3, A1/A2/A3 — translation / velocity / acceleration components
# - RF1/RF2/RF3 — reaction force components
# - S11/S22/.../S12 — stress tensor components
# - E11/.../E12 — strain tensor components
# - Whole-model energies (ALLAE / ALLKE / ETOTAL / ...) have no component.
_COMP_SUFFIX_AXES = {"1": "x", "2": "y", "3": "z"}


def _history_component_for(name: str) -> str:
    """Best-effort component name for the picker. Empty string when the
    variable has no natural component (energy, single-scalar metrics)."""

    if len(name) >= 2 and name[-1] in _COMP_SUFFIX_AXES:
        head = name.rstrip("0123456789")
        if head in ("U", "V", "A", "RF", "RM", "UR", "VR", "AR", "CF"):
            return _COMP_SUFFIX_AXES[name[-1]]
    return ""


def _history_group_for(name: str) -> str:
    """Cluster key — variables sharing a group get plotted together in
    the picker. ``U1`` / ``U2`` / ``U3`` → ``U``. Returns the full name
    when no obvious grouping exists, so unrelated variables stay
    distinct."""

    head = name.rstrip("0123456789")
    if (
        head
        and head != name
        and head
        in (
            "U",
            "V",
            "A",
            "RF",
            "RM",
            "UR",
            "VR",
            "AR",
            "CF",
            "S",
            "E",
            "EE",
            "PE",
            "LE",
            "NE",
        )
    ):
        return head
    return name


class ResultsSqliteStreamReader:
    """Stream a results ``.sqlite`` through adapy's ``FEAStreamReader`` protocol.

    Multi-instance ODBs (e.g. a jacket + topside + container model)
    get every instance's nodes + elements concatenated
    into a single ``MeshGeometry``. Each instance's points sit in a
    contiguous slab in the combined ``points`` array; cell blocks are
    emitted per (instance, meshio_type) so field-bake renderers that
    rely on ``cell_blocks`` order still hit one block per logical
    chunk. Field iteration scatters each instance's BLOB into the
    correct slab of the combined value array.

    Beam-as-solid emission still needs section + axis metadata that
    the current schema doesn't expose; ``try_solid_beams`` returns
    ``None``.
    """

    def __init__(self, sqlite_path: os.PathLike):
        self._path = pathlib.Path(sqlite_path)
        self._conn = sqlite3.connect(str(self._path))
        if not is_results_db(self._conn):
            self._conn.close()
            raise ValueError(f"{self._path} is not an FEA results sqlite (no Points / FieldNodes tables)")
        check_schema_version(self._conn, self._path.name)
        self._conn.row_factory = sqlite3.Row
        # Sorted list of every InstanceID that has at least one row in
        # Points. ASSEMBLY (ID=0) carries no instance-level nodes in
        # practice — the mesh lives under per-Instance IDs — but the
        # ``EXISTS`` filter handles the edge case automatically.
        self._instance_ids: list[int] = self._read_instance_ids()
        # Per-instance label → local-row (within the instance's slab).
        # Field-bulk data references nodes by Abaqus label; a nodal
        # blob is indexed by sorted-label position within the
        # instance. We rebuild the same map by re-reading Points
        # ordered by label.
        self._inst_point_index: dict[int, dict[int, int]] = {}
        # Per-instance offset into the combined ``points`` array. A
        # field blob from instance ``i`` of length ``N_i`` lands at
        # ``arr[offset[i]:offset[i]+N_i, col]``.
        self._inst_point_offset: dict[int, int] = {}
        self._inst_n_points: dict[int, int] = {}
        self._n_points_total: int = 0
        self._geom: MeshGeometry | None = None
        self._field_specs_cache: list[FieldSpec] | None = None
        # FieldVarID → (description, component_name)
        self._field_var_lookup: dict[int, tuple[str, str]] | None = None
        # Description → ordered list[FieldVarID] (matches FieldSpec.components order)
        self._field_groups: dict[str, list[int]] | None = None
        self._element_field_specs_cache: list[ElementFieldSpec] | None = None
        # (spec.name, spec.elem_type) → list of per-instance buckets.
        # Each bucket: (instance_id, abq_elem_type, field_var_ids,
        # element_offset, n_elements_in_instance). element_offset is
        # the running index into spec.element_labels at which this
        # instance's elements start — lets iter_element_field_steps
        # scatter each per-instance blob into the right slab of arr.
        self._element_field_buckets: (
            dict[
                tuple[str, str, str],
                list[tuple[int, str, str, list[int], int, int]],
            ]
            | None
        ) = None
        self._ensure_indexes()

    # ----- setup ----------------------------------------------------------

    def _read_instance_ids(self) -> list[int]:
        cur = self._conn.execute(
            "SELECT mi.ID FROM ModelInstances mi "
            "WHERE EXISTS ("
            "  SELECT 1 FROM Points p WHERE p.InstanceID = mi.ID"
            ") "
            "ORDER BY mi.ID"
        )
        rows = cur.fetchall()
        if not rows:
            # Tell a truly empty database from an empty mesh.
            cur = self._conn.execute("SELECT ID FROM ModelInstances ORDER BY ID LIMIT 1")
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"no rows in ModelInstances; {self._path} holds no " f"exported model")
            raise ValueError(
                f"no Points rows for any InstanceID in {self._path}; " f"the export ran but produced an empty mesh"
            )
        return [int(r[0]) for r in rows]

    def _ensure_indexes(self) -> None:
        # The schema ships no indexes. Add the ones the streaming
        # protocol needs so per-step queries don't scan the whole
        # FieldNodes / FieldElem tables. The blob-packed schema has
        # one row per (Instance, Step, FieldVar, Frame[, Location,
        # ElemType]) rather than one per value, so the per-step query
        # is naturally cheap — but a covering index still pays off
        # because the reader picks specs by FieldVar and steps by
        # Frame. Idempotent.
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_points_inst_id ON Points(InstanceID, ID)",
            "CREATE INDEX IF NOT EXISTS idx_conn_inst_elem_seq ON ElementConnectivity(InstanceID, ElemID, Seq)",
            "CREATE INDEX IF NOT EXISTS idx_einfo_inst_type_elem ON ElementInfo(InstanceID, Type, ElemID)",
            "CREATE INDEX IF NOT EXISTS idx_fnodes_inst_fvar_frame ON FieldNodes(InstanceID, FieldVarID, Frame)",
            "CREATE INDEX IF NOT EXISTS idx_felem_inst_fvar_type_frame "
            " ON FieldElem(InstanceID, FieldVarID, ElemType, Frame)",
            "CREATE INDEX IF NOT EXISTS idx_fvars_id ON FieldVars(FieldID)",
        ):
            self._conn.execute(stmt)
        self._conn.commit()

    # ----- protocol -------------------------------------------------------

    def read_mesh_geometry(self) -> MeshGeometry:
        if self._geom is not None:
            return self._geom

        # Walk every instance's Points slab in order, building the
        # combined points array + the per-instance label→local-row
        # map. ``_inst_point_offset`` lets the field readers slot each
        # instance's blob into the right slab of the combined value
        # array.
        all_points: list[np.ndarray] = []
        offset = 0
        for inst_id in self._instance_ids:
            rows = self._conn.execute(
                "SELECT ID, X, Y, Z FROM Points WHERE InstanceID = ? ORDER BY ID",
                (inst_id,),
            ).fetchall()
            if not rows:
                # Should not happen — _read_instance_ids filters on
                # EXISTS(Points) — but tolerate it so an inconsistent
                # sqlite degrades to "missing instance" rather than crash.
                self._inst_point_offset[inst_id] = offset
                self._inst_n_points[inst_id] = 0
                self._inst_point_index[inst_id] = {}
                continue
            n = len(rows)
            point_ids = np.empty(n, dtype=np.int64)
            inst_pts = np.empty((n, 3), dtype=np.float64)
            for i, r in enumerate(rows):
                point_ids[i] = r[0]
                inst_pts[i, 0] = r[1]
                inst_pts[i, 1] = r[2]
                inst_pts[i, 2] = r[3]
            self._inst_point_index[inst_id] = dict(zip(point_ids.tolist(), range(n)))
            self._inst_point_offset[inst_id] = offset
            self._inst_n_points[inst_id] = n
            all_points.append(inst_pts)
            offset += n

        if not all_points:
            raise ValueError(
                f"no Points rows found for any of {len(self._instance_ids)} " f"instance(s) in {self._path}"
            )
        points = np.vstack(all_points)
        self._n_points_total = int(points.shape[0])

        # Connectivity: one query per (instance, type). The previous
        # single-query-per-instance path JOIN'd ElementConnectivity to
        # ElementInfo and re-bucketed by Type in Python via setdefault
        # + per-element ``sorted()`` — 16+ s of pure dict / list churn
        # on a large multi-instance model. The per-type path drops that:
        #
        #   - Rows already arrive ORDER BY ElemID, Seq → no Python sort.
        #   - n_elements is known up-front via the GROUP BY → numpy
        #     arrays are pre-sized, no growth.
        #   - All elements of one Abaqus type have the same node-count,
        #     so ``len(rows) // n_elements`` is the nodes-per-element
        #     and we ``reshape`` straight into the (n_elements, nodes)
        #     connectivity matrix.
        cell_blocks: list[CellBlockData] = []
        for inst_id in self._instance_ids:
            if self._inst_n_points.get(inst_id, 0) == 0:
                continue
            type_rows = self._conn.execute(
                "SELECT Type, COUNT(*) FROM ElementInfo WHERE InstanceID = ? GROUP BY Type ORDER BY Type",
                (inst_id,),
            ).fetchall()
            if not type_rows:
                continue

            point_index = self._inst_point_index[inst_id]
            inst_offset = self._inst_point_offset[inst_id]

            for abq_type, n_elements in type_rows:
                if abq_type in _SKIP_RENDER_TYPES:
                    # Point masses / rotary inertia carry no geometry
                    # the renderer can use. Raw FieldElem rows still
                    # live in sqlite for non-protocol consumers.
                    continue
                meshio_type = _ABAQUS_TYPE_TO_MESHIO.get(abq_type)
                if meshio_type is None:
                    raise ValueError(
                        f"Abaqus element type {abq_type!r} not mapped to a "
                        f"meshio cell_type; extend _ABAQUS_TYPE_TO_MESHIO."
                    )
                # idx_einfo_inst_type_elem covers the ei.Type predicate;
                # idx_conn_inst_elem_seq orders the connectivity rows.
                conn_rows = self._conn.execute(
                    "SELECT ec.ElemID, ec.PointID "
                    "FROM ElementConnectivity ec "
                    "JOIN ElementInfo ei "
                    " ON ec.InstanceID = ei.InstanceID "
                    " AND ec.ElemID = ei.ElemID "
                    "WHERE ec.InstanceID = ? AND ei.Type = ? "
                    "ORDER BY ec.ElemID, ec.Seq",
                    (inst_id, abq_type),
                ).fetchall()
                if not conn_rows:
                    continue
                total = len(conn_rows)
                nodes_per = total // n_elements
                if nodes_per <= 0 or nodes_per * n_elements != total:
                    raise ValueError(
                        f"connectivity row count {total} not divisible by "
                        f"n_elements {n_elements} for "
                        f"(inst={inst_id}, type={abq_type!r})"
                    )

                # Vectorized label → combined-array-row lookup. dict.get
                # on a Python int is the cheapest map we have here — a
                # numpy gather would need a label→row contiguous lookup
                # table whose size is unbounded in pathological label
                # sparsity. The list-comp keeps allocation costs flat.
                point_ids_iter = (r[1] for r in conn_rows)
                data_flat = np.fromiter(
                    (point_index[pid] + inst_offset for pid in point_ids_iter),
                    dtype=np.int64,
                    count=total,
                )
                data = data_flat.reshape((n_elements, nodes_per))

                # ElemIDs of unique elements are every nodes_per'th row
                # — the ORDER BY ElemID, Seq guarantee makes them
                # contiguous.
                elem_ids = np.fromiter(
                    (conn_rows[i][0] for i in range(0, total, nodes_per)),
                    dtype=np.int64,
                    count=n_elements,
                )

                cell_blocks.append(
                    CellBlockData(
                        cell_type=meshio_type,
                        data=data,
                        identifiers=elem_ids,
                    )
                )

        self._geom = MeshGeometry(points=points, cell_blocks=cell_blocks)
        return self._geom

    def field_specs(self) -> list[FieldSpec]:
        if self._field_specs_cache is not None:
            return self._field_specs_cache

        # Pull every FieldVar; group by Description so a vector/tensor
        # field stored as N component rows comes back as
        # one FieldSpec with components = [name_for_each_row].
        fv_rows = self._conn.execute("SELECT FieldID, Name, Description FROM FieldVars ORDER BY FieldID").fetchall()
        groups: dict[str, list[tuple[int, str]]] = {}
        for fid, name, desc in fv_rows:
            groups.setdefault(desc or "", []).append((int(fid), name))
        groups = {desc: order_components(members) for desc, members in groups.items()}

        self._field_var_lookup = {int(fid): (desc or "", name) for fid, name, desc in fv_rows}

        # n_points anchors every nodal blob's payload shape — must
        # equal what read_mesh_geometry produced (combined across
        # every instance's slab).
        n_points = int(self.read_mesh_geometry().points.shape[0])

        specs: list[FieldSpec] = []
        field_groups: dict[str, list[int]] = {}
        for desc, members in groups.items():
            field_var_ids = [m[0] for m in members]
            components = [m[1] for m in members]

            # Frames that have nodal data for this field, looking
            # across every instance the reader covers. A multi-
            # instance ODB typically has the same (StepID, Frame)
            # set per instance; DISTINCT collapses the duplicates.
            step_rows = self._conn.execute(
                f"SELECT DISTINCT StepID, Frame FROM FieldNodes "
                f"WHERE FieldVarID IN ({','.join('?' * len(field_var_ids))}) "
                f"  AND IsImaginary = 0 "
                f"ORDER BY StepID, Frame",
                tuple(field_var_ids),
            ).fetchall()
            if not step_rows:
                # Field exists in FieldVars but has no nodal data —
                # likely an element-only field (e.g. integration-point
                # stress). Skipped for Stage 2 nodal-only.
                continue

            step_values = [float(r[1]) for r in step_rows]
            name = desc or components[0]
            specs.append(
                FieldSpec(
                    name=name,
                    components=components,
                    n_steps=len(step_rows),
                    n_points=n_points,
                    support="nodal",
                    step_values=step_values,
                    category=_classify_abaqus_field(desc, components),
                )
            )
            field_groups[name] = field_var_ids

        self._field_groups = field_groups
        self._field_specs_cache = specs
        return specs

    def iter_field_steps(self, field_name: str) -> Iterator[StepValues]:
        # Force spec build so _field_groups is populated.
        specs = self.field_specs()
        spec = next((s for s in specs if s.name == field_name), None)
        if spec is None or self._field_groups is None:
            raise KeyError(field_name)
        field_var_ids = self._field_groups[field_name]

        # Map FieldVarID → component column index (0-based, matches
        # FieldSpec.components order).
        var_to_col = {fid: i for i, fid in enumerate(field_var_ids)}

        # Enumerate the (StepID, Frame) pairs for this field across
        # all instances; spec.n_steps already collapsed duplicates.
        step_rows = self._conn.execute(
            f"SELECT DISTINCT StepID, Frame FROM FieldNodes "
            f"WHERE FieldVarID IN ({','.join('?' * len(field_var_ids))}) "
            f"ORDER BY StepID, Frame",
            tuple(field_var_ids),
        ).fetchall()

        for i, (step_id, frame) in enumerate(step_rows):
            arr = np.zeros((spec.n_points, spec.n_components), dtype=np.float32)
            # Per-step: each instance's blob is one row. Slot each
            # into its slab of the combined arr at the instance's
            # point offset; trailing slabs that never write stay 0
            # (matches the old per-row reader's orphan-row policy).
            for inst_id in self._instance_ids:
                offset = self._inst_point_offset.get(inst_id, 0)
                n_pts_inst = self._inst_n_points.get(inst_id, 0)
                if n_pts_inst == 0:
                    continue
                cursor = self._conn.execute(
                    f"SELECT FieldVarID, Data FROM FieldNodes "
                    f"WHERE InstanceID = ? AND StepID = ? AND Frame = ? "
                    f"  AND IsImaginary = 0 "
                    f"  AND FieldVarID IN ({','.join('?' * len(field_var_ids))})",
                    (inst_id, int(step_id), float(frame), *field_var_ids),
                )
                for fvar_id, blob in cursor:
                    col = var_to_col.get(int(fvar_id))
                    if col is None:
                        continue
                    values = np.frombuffer(blob, dtype=np.float32)
                    n = min(values.shape[0], n_pts_inst)
                    arr[offset : offset + n, col] = values[:n]
            yield StepValues(
                step_index=i,
                step_value=float(frame),
                values=arr,
            )

    def element_field_specs(self) -> list[ElementFieldSpec]:
        if self._element_field_specs_cache is not None:
            return self._element_field_specs_cache

        # Force the nodal pass so _field_var_lookup is populated — same
        # FieldVars groups feed both nodal and element specs.
        self.field_specs()
        assert self._field_var_lookup is not None

        # All (Description, ElemType, Location) combos that have
        # FieldElem data anywhere. The Location axis (INTEGRATION_POINT
        # / ELEMENT_NODAL / CENTROID / ...) becomes a separate spec
        # because adapy's ``ElementFieldSpec.support`` distinguishes
        # gauss-position-resolved vs element-nodal-extrapolated data;
        # collapsing them at the same time would conflate field shape.
        bucket_rows = self._conn.execute(
            "SELECT DISTINCT fv.Description, fe.ElemType, fe.Location "
            "FROM FieldElem fe "
            "JOIN FieldVars fv ON fv.FieldID = fe.FieldVarID "
            "WHERE fe.IsImaginary = 0 "
            "ORDER BY fv.Description, fe.ElemType, fe.Location"
        ).fetchall()

        specs: list[ElementFieldSpec] = []
        # Per (name, meshio_type, support) → list of per-instance buckets.
        # Each per-instance entry carries the field_var_ids, the offset
        # into the spec's combined element axis, and n_elements_in_inst.
        buckets: dict[
            tuple[str, str, str],
            list[tuple[int, str, str, list[int], int, int]],
        ] = {}
        for desc, abq_type, location in bucket_rows:
            desc = desc or ""
            location = location or ""
            if abq_type in _SKIP_RENDER_TYPES:
                continue
            meshio_type = _ABAQUS_TYPE_TO_MESHIO.get(abq_type)
            if meshio_type is None:
                raise ValueError(
                    f"Abaqus element type {abq_type!r} (element field "
                    f"{desc!r}) not mapped to a meshio cell_type; "
                    f"extend _ABAQUS_TYPE_TO_MESHIO."
                )

            support = _location_to_support(location)

            fv_rows = self._conn.execute(
                "SELECT FieldID, Name FROM FieldVars WHERE Description = ? ORDER BY FieldID",
                (desc,),
            ).fetchall()
            fv_rows = order_components([(int(fid), name) for fid, name in fv_rows])
            field_var_ids = [fid for fid, _ in fv_rows]
            components = [name for _, name in fv_rows]
            if not field_var_ids:
                continue

            # Walk every instance: pull its (Type, ElemID)-ordered
            # element list and accumulate combined element_labels.
            combined_labels: list[int] = []
            inst_buckets: list[tuple[int, str, str, list[int], int, int]] = []
            n_ips_combined = 0
            all_step_values_set: set[tuple[int, float]] = set()
            for inst_id in self._instance_ids:
                elem_rows = self._conn.execute(
                    "SELECT ElemID FROM ElementInfo WHERE InstanceID = ? AND Type = ? ORDER BY ElemID",
                    (inst_id, abq_type),
                ).fetchall()
                if not elem_rows:
                    continue

                # Does this instance carry FieldElem rows for this
                # (desc, type, location) bucket?
                ok_row = self._conn.execute(
                    f"SELECT 1 FROM FieldElem "
                    f"WHERE InstanceID = ? AND ElemType = ? AND Location = ? "
                    f"  AND IsImaginary = 0 "
                    f"  AND FieldVarID IN ({','.join('?' * len(field_var_ids))}) "
                    f"LIMIT 1",
                    (inst_id, abq_type, location, *field_var_ids),
                ).fetchone()
                if ok_row is None:
                    continue

                inst_labels = [int(r[0]) for r in elem_rows]
                offset = len(combined_labels)
                combined_labels.extend(inst_labels)
                inst_buckets.append((inst_id, abq_type, location, list(field_var_ids), offset, len(inst_labels)))

                n_ips_row = self._conn.execute(
                    f"SELECT NIPs FROM FieldElem "
                    f"WHERE InstanceID = ? AND ElemType = ? AND Location = ? "
                    f"  AND IsImaginary = 0 "
                    f"  AND FieldVarID IN ({','.join('?' * len(field_var_ids))}) "
                    f"LIMIT 1",
                    (inst_id, abq_type, location, *field_var_ids),
                ).fetchone()
                n_ips_inst = int(n_ips_row[0]) if n_ips_row and n_ips_row[0] else 1
                if n_ips_inst > n_ips_combined:
                    n_ips_combined = n_ips_inst

                for sid, frm in self._conn.execute(
                    f"SELECT DISTINCT StepID, Frame FROM FieldElem "
                    f"WHERE InstanceID = ? AND ElemType = ? AND Location = ? "
                    f"  AND IsImaginary = 0 "
                    f"  AND FieldVarID IN ({','.join('?' * len(field_var_ids))})",
                    (inst_id, abq_type, location, *field_var_ids),
                ).fetchall():
                    all_step_values_set.add((int(sid), float(frm)))

            if not combined_labels or not all_step_values_set:
                continue
            if n_ips_combined < 1:
                n_ips_combined = 1
            step_sorted = sorted(all_step_values_set)
            step_values = [frm for _, frm in step_sorted]

            # When multiple Location buckets exist for the same (desc,
            # type), append a suffix to the spec name so they don't
            # collide in the protocol's name space. Single-Location
            # specs keep the bare ``desc``.
            base_name = desc or components[0]
            name = base_name
            existing_for_base = sum(1 for s in specs if s.name.startswith(base_name) and s.elem_type == meshio_type)
            if existing_for_base > 0:
                name = f"{base_name} ({support})"
            specs.append(
                ElementFieldSpec(
                    name=name,
                    components=components,
                    n_steps=len(step_sorted),
                    elem_type=meshio_type,
                    n_elements=len(combined_labels),
                    n_ips=n_ips_combined,
                    element_labels=combined_labels,
                    step_values=step_values,
                    ip_layout=[],
                    category=_classify_abaqus_field(desc, components),
                    support=support,
                )
            )
            buckets[(name, meshio_type, support)] = inst_buckets

        self._element_field_buckets = buckets
        self._element_field_specs_cache = specs
        return specs

    def iter_element_field_steps(self, spec: ElementFieldSpec) -> Iterator[ElementStepValues]:
        # Force the spec pass so _element_field_buckets is populated.
        self.element_field_specs()
        assert self._element_field_buckets is not None

        inst_buckets = self._element_field_buckets.get((spec.name, spec.elem_type, spec.support))
        if not inst_buckets:
            raise KeyError((spec.name, spec.elem_type, spec.support))

        first_field_var_ids = inst_buckets[0][3]
        var_to_col = {fid: i for i, fid in enumerate(first_field_var_ids)}

        # Collect the (StepID, Frame) set across instances. Filter on
        # the same Location bucket the spec was built from so two
        # Location buckets of the same field don't bleed steps into
        # each other.
        step_pairs: list[tuple[int, float]] = []
        seen: set[tuple[int, float]] = set()
        for inst_id, abq_type, location, field_var_ids, _, _ in inst_buckets:
            for sid, frm in self._conn.execute(
                f"SELECT DISTINCT StepID, Frame FROM FieldElem "
                f"WHERE InstanceID = ? AND ElemType = ? AND Location = ? "
                f"  AND IsImaginary = 0 "
                f"  AND FieldVarID IN ({','.join('?' * len(field_var_ids))})",
                (inst_id, abq_type, location, *field_var_ids),
            ).fetchall():
                key = (int(sid), float(frm))
                if key not in seen:
                    seen.add(key)
                    step_pairs.append(key)
        step_pairs.sort()

        for i, (step_id, frame) in enumerate(step_pairs):
            arr = np.zeros(
                (spec.n_elements, spec.n_ips, spec.n_components),
                dtype=np.float32,
            )
            for inst_id, abq_type, location, field_var_ids, offset, n_inst in inst_buckets:
                cursor = self._conn.execute(
                    f"SELECT FieldVarID, NIPs, Data FROM FieldElem "
                    f"WHERE InstanceID = ? AND ElemType = ? AND Location = ? "
                    f"  AND StepID = ? AND Frame = ? "
                    f"  AND IsImaginary = 0 "
                    f"  AND FieldVarID IN ({','.join('?' * len(field_var_ids))})",
                    (
                        inst_id,
                        abq_type,
                        location,
                        int(step_id),
                        float(frame),
                        *field_var_ids,
                    ),
                )
                for fvar_id, n_ips_row, blob in cursor:
                    col = var_to_col.get(int(fvar_id))
                    if col is None:
                        continue
                    n_ips_local = int(n_ips_row) if n_ips_row else 1
                    if n_ips_local < 1:
                        n_ips_local = 1
                    values = np.frombuffer(blob, dtype=np.float32)
                    # Reshape per-instance: (n_inst, n_ips_local). If
                    # the bucket's n_ips_local matches spec.n_ips we
                    # slot straight in; if it's smaller (rare — same
                    # elem type with mixed IP counts across instances
                    # is unusual) we copy into the first n_ips_local
                    # IP slots.
                    expected = n_inst * n_ips_local
                    if values.shape[0] >= expected:
                        v2d = values[:expected].reshape((n_inst, n_ips_local))
                    else:
                        # Truncated blob — accept the whole-element prefix.
                        n_complete = values.shape[0] // n_ips_local
                        if n_complete <= 0:
                            continue
                        v2d = values[: n_complete * n_ips_local].reshape((n_complete, n_ips_local))
                    ips_to_write = min(n_ips_local, spec.n_ips)
                    rows_to_write = v2d.shape[0]
                    arr[offset : offset + rows_to_write, :ips_to_write, col] = v2d[:, :ips_to_write]
            yield ElementStepValues(
                step_index=i,
                step_value=float(frame),
                values=arr,
            )

    def try_solid_beams(self):
        # The current sqlite schema carries no section / axis metadata,
        # so beam-as-solid tessellation isn't reachable from here yet.
        return None

    def try_history_records(self) -> HistoryRecords | None:
        """Build the manifest's history section from the HistOutput table.

        The exporter records one row per (region, variable, step,
        frame) sample. We pull the joined view, partition by region +
        variable + step, and sort each partition by frame so the
        manifest carries clean (times, values) arrays. Returns ``None``
        when the analysis recorded no history output (common for
        eigen-only or pure-field bakes).
        """

        cur = self._conn.execute(
            "SELECT mi.Name, ho.ResType, ho.Region, ho.InstanceID, "
            "       ho.PointID, ho.ElemID, ho.StepID, st.Name, "
            "       ho.FieldVarID, fv.Name, fv.Description, "
            "       ho.Frame, ho.Value "
            "FROM HistOutput ho "
            "JOIN FieldVars fv ON fv.FieldID = ho.FieldVarID "
            "JOIN ModelInstances mi ON mi.ID = ho.InstanceID "
            "JOIN Steps st ON st.ID = ho.StepID "
            "ORDER BY ho.InstanceID, ho.PointID, ho.ElemID, ho.Region, "
            "         ho.FieldVarID, ho.StepID, ho.Frame"
        )
        rows = cur.fetchall()
        if not rows:
            return None

        regions: dict[str, HistoryRegion] = {}
        variables: dict[str, HistoryVariable] = {}
        steps: dict[int, HistoryStep] = {}
        series_map: dict[tuple[str, str, int], HistorySeries] = {}

        for (
            inst_name,
            res_type,
            region_name,
            inst_id,
            point_id,
            elem_id,
            step_id,
            step_name,
            field_var_id,
            fv_name,
            fv_desc,
            frame,
            value,
        ) in rows:
            kind, region_id = _classify_history_region(
                res_type,
                inst_id,
                point_id,
                elem_id,
                region_name,
            )
            if region_id not in regions:
                coords = self._lookup_point_coords(inst_id, point_id) if kind == "node" else None
                display = _format_region_display(
                    kind,
                    inst_name,
                    point_id,
                    elem_id,
                    region_name,
                )
                regions[region_id] = HistoryRegion(
                    id=region_id,
                    kind=kind,
                    instance=inst_name or "",
                    label=region_name or "",
                    display_name=display,
                    coords=coords,
                )

            if fv_name not in variables:
                variables[fv_name] = HistoryVariable(
                    name_native=fv_name,
                    name_canonical=fv_name,
                    category=_classify_abaqus_field(fv_desc or "", [fv_name]),
                    component=_history_component_for(fv_name),
                    group=_history_group_for(fv_name),
                )

            if step_id not in steps:
                steps[step_id] = HistoryStep(
                    i=int(step_id),
                    name=step_name or f"step_{step_id}",
                )

            key = (region_id, fv_name, int(step_id))
            ser = series_map.get(key)
            if ser is None:
                ser = HistorySeries(
                    region_id=region_id,
                    variable=fv_name,
                    step_idx=int(step_id),
                    times=[],
                    values=[],
                )
                series_map[key] = ser
            ser.times.append(float(frame))
            ser.values.append(float(value))

        step_index_map = {sid: i for i, sid in enumerate(sorted(steps))}
        sorted_steps = [
            HistoryStep(
                i=step_index_map[sid],
                name=steps[sid].name,
                procedure=steps[sid].procedure,
                domain=steps[sid].domain,
            )
            for sid in sorted(steps)
        ]
        for ser in series_map.values():
            ser.step_idx = step_index_map[ser.step_idx]

        return HistoryRecords(
            regions=list(regions.values()),
            variables=list(variables.values()),
            steps=sorted_steps,
            series=list(series_map.values()),
        )

    def _lookup_point_coords(
        self,
        instance_id: int,
        point_id: int,
    ) -> tuple[float, float, float] | None:
        if point_id is None or point_id < 0:
            return None
        row = self._conn.execute(
            "SELECT X, Y, Z FROM Points WHERE InstanceID = ? AND ID = ?",
            (int(instance_id), int(point_id)),
        ).fetchone()
        if row is None:
            return None
        return (float(row[0]), float(row[1]), float(row[2]))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ResultsSqliteStreamReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def open_results_sqlite(path: os.PathLike) -> ResultsSqliteStreamReader:
    return ResultsSqliteStreamReader(pathlib.Path(path))


def open_odb(path: os.PathLike) -> ResultsSqliteStreamReader:
    """Export the ``.odb`` to the ``.sqlite`` beside it (reused while newer than the ODB), then open it."""
    from ada.fem.formats.abaqus.results.read_odb import convert_odb_to_sqlite

    return ResultsSqliteStreamReader(convert_odb_to_sqlite(path))


def register(odb: bool = True) -> None:
    """Add the results-SQLite stream readers to the registry: ``.sqlite``, and ``.odb`` with ``odb``.

    Opt-in, because a worker advertises every registered suffix: register ``.odb`` only where
    Abaqus is installed (the export runs ``abaqus python``), and ``.sqlite`` only where a
    ``.sqlite`` source means an FEA result. Explicit registration overrides an earlier one for the
    same suffix.
    """
    from ada.fem.results.artefacts import register_stream_reader

    register_stream_reader(".sqlite", open_results_sqlite)
    if odb:
        register_stream_reader(".odb", open_odb)

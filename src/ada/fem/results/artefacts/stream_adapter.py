"""Adapt an in-memory FEAResult to the streaming reader protocol."""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ada.fem.results.common import CellBlockData

from .beam_compact import BeamSolidInstances
from .beam_solids import tessellate_beams_to_solid_mesh
from .manifest import analysis_kind_from_result_cases
from .specs import (
    ElementFieldSpec,
    ElementStepValues,
    FieldCategory,
    FieldSpec,
    MeshGeometry,
    SolidBeamMesh,
    StepValues,
)


def _ip_layout_from_int_positions(int_positions) -> list[dict]:
    """Best-effort IP-layout metadata for the frontend's layer + IP
    pickers. The Sesam reader populates ``int_positions`` from its
    ``INT_LOCATIONS`` table — a list of ``(ip_id, in_plane, layer)``
    tuples where ``layer`` is -0.5 for the bottom fibre, +0.5 for
    the top, 0 for mid. ``in_plane`` is either a corner-node index
    or a centroid-ish tuple. Other readers leave it as ``None``;
    return an empty list and let the frontend fall back to numeric
    IP indices.

    Output: one dict per integration point, in the original list
    order. Keys: ``ip`` (0-based), ``layer`` ("top" | "bottom" |
    "mid" | numeric string), ``in_plane`` (free-form string).
    """

    if not int_positions:
        return []

    layout: list[dict] = []
    for entry in int_positions:
        # Tolerate any reasonable tuple shape from the readers; keep
        # the metadata advisory rather than load-bearing.
        ip_id = None
        in_plane = None
        layer_val = None
        if isinstance(entry, (list, tuple)):
            if len(entry) >= 1:
                ip_id = entry[0]
            if len(entry) >= 2:
                in_plane = entry[1]
            if len(entry) >= 3:
                layer_val = entry[2]
        layer_label: str
        if isinstance(layer_val, (int, float)):
            lv = float(layer_val)
            if lv > 0:
                layer_label = "top"
            elif lv < 0:
                layer_label = "bottom"
            else:
                layer_label = "mid"
        else:
            layer_label = "mid"
        layout.append(
            {
                "ip": ip_id if isinstance(ip_id, int) else len(layout),
                "layer": layer_label,
                "in_plane": str(in_plane) if in_plane is not None else "",
                **(
                    {"node_index": int(in_plane)}
                    if isinstance(in_plane, (int, np.integer))
                    else (
                        {"natural_coordinates": [float(value) for value in in_plane]}
                        if isinstance(in_plane, (list, tuple))
                        and all(isinstance(value, (int, float, np.integer, np.floating)) for value in in_plane)
                        else (
                            {"natural_coordinates": [float(in_plane)]}
                            if isinstance(in_plane, (float, np.floating))
                            else {}
                        )
                    )
                ),
            }
        )
    return layout


def _classify_field(name: str, sample) -> FieldCategory:
    """Best-effort field-category tag from the field name + the
    sample :class:`FieldData` payload.

    Two signals: an explicit ``field_type`` enum on the sample (only
    ``NodalFieldData`` carries one today — ``DISP`` / ``FORCE`` /
    ``VEL`` / ``UNKNOWN``) and the field name (e.g. Sesam RVNODDIS,
    RVSTRESS, RVFORCES). The frontend uses the category to decide
    whether to drive mesh deformation off a field; misclassification
    just means the user gets the warp toggle defaulted wrong, which
    they can fix in the UI. So the fallback is ``other``, not a hard
    failure.
    """

    from ada.fem.results.field_data import NodalFieldType

    field_type = getattr(sample, "field_type", None)
    if field_type is not None:
        if field_type == NodalFieldType.DISP:
            return "displacement"
        # NodalFieldType.FORCE on a nodal output is a reaction force
        # in every solver we currently read; if that ever stops being
        # true, the reader can override by passing category=... on
        # the spec construction directly.
        if field_type == NodalFieldType.FORCE:
            return "reaction"

    # Model-property fields (thickness, material, section) are input data, not
    # analysis output; results pickers hide the category and a properties
    # panel lists it. The namespace is the contract — see property_fields.py.
    if name.startswith("props."):
        return "property"

    upper = name.upper()
    # Sesam RVNODDIS = nodal displacements; RVFORCES = beam-element
    # section forces (not nodal reactions); RVSTRESS = element
    # stresses. Code Aster: DEPL / SIEF / EPSI. Generic Abaqus /
    # MED: U / S / E.
    if any(token in upper for token in ("DISP", "DEPL", "RVNODDIS")):
        return "displacement"
    if any(token in upper for token in ("REAC", "RF", "RVFORCES")):
        return "reaction"
    if any(token in upper for token in ("STRESS", "SIGMA", "SIEF", "RVSTRESS")):
        return "stress"
    if any(token in upper for token in ("STRAIN", "EPSI", "EPS")):
        return "strain"
    return "other"


class FEAResultStreamAdapter:
    """Wrap an in-memory :class:`FEAResult` so the bake can consume it
    through the streaming reader interface.

    No real streaming benefit — the adapter already has the full
    FEAResult — but it lets formats that haven't been rewritten as
    native streamers (SIF, FRD) flow through the same artefact
    pipeline. When a big-model SIF or FRD case actually OOMs the
    bake, the answer is to write a native streaming reader for that
    format and replace the adapter for that format only; the
    artefact code on top doesn't change.
    """

    def __init__(self, result, *, load_results=None, on_close=None):
        self._result = result
        # ``load_results``: the result's fields are not read yet (its mesh is). Called
        # once, the first time anything needs a field -- so a bake writes and ships
        # the mesh while the fields are still being decoded behind it, instead of
        # after the whole file was read (see readers._make_sin_reader).
        # ``on_close``: releases what ``load_results`` would have read from, when
        # the bake stops before it ran.
        self._load_results = load_results
        self._on_close = on_close
        self._geom: MeshGeometry | None = None
        self._field_specs: list[FieldSpec] | None = None
        self._elem_field_specs: list[ElementFieldSpec] | None = None
        # {(field name, element type): element labels in the spec's order}, set by element_field_specs.
        self._elem_field_labels: dict[tuple[str, str], list[int]] = {}
        # Optional FEA input concepts (masses / BCs / load scenarios) as a manifest-shaped
        # dict. SIF/SIN leave this None; the FEM reader (_make_fem_reader) scrapes it from the
        # deck so the Scene > FEM panel can draw the glyph overlay.
        self._fem_concepts: dict | None = None
        # Optional FEM node/element sets as manifest group dicts ({name, members, fe_object_type}).
        # Populated by the FEM reader for design models, and carried on the result by
        # the Sesam readers, which decode TDSETNAM / GSETMEMB while parsing the deck.
        self._groups: list[dict] | None = getattr(result, "sesam_groups", None)
        # What the deck calls each result case, keyed by case number. A step is
        # otherwise labelled with its value — "1", "2", "3" — which is an index,
        # where the deck's own `unit_acc_x` is a load case.
        self._step_names: dict[int, str] | None = getattr(result, "sesam_case_names", None)
        # Every case the deck OFFERS — which is more than it stores. A "smart
        # load combination" deck records the basic cases and defines the design
        # cases as combinations of them, so a picker built from the steps offers
        # the cases nobody checks and omits the ones they do.
        self._result_cases: list[dict] | None = getattr(result, "sesam_result_cases", None)

        # Remap real node IDs → 0-based point indices. ElementBlock
        # stores arbitrary-id node references (1-based for RMED,
        # arbitrary for SIF/FRD); the artefact pipeline expects
        # 0-based indices into the points array.
        ids = result.mesh.nodes.identifiers
        self._nmap = {int(x): i for i, x in enumerate(ids)}
        # The element numbers people read, where they differ from the ids the mesh and
        # results are keyed by (a Sesam deck whose external numbers are not its internal
        # ones). Everything the bake writes for the viewer -- the mesh sidecars' element
        # labels, element-field labels, beam-solid labels -- carries the shown number, so
        # the viewer agrees with the deck's own tools. ``None``: they are the same.
        label_map = getattr(result.mesh, "element_label_map", None)
        self._elem_shown: dict[int, int] | None = label_map() if callable(label_map) else None
        # Sets the reader made rather than the deck (GeniE's dummy-mass beams), tagged
        # with their role so the viewer can treat them differently. From the mesh, which
        # also holds a merged assembly's.
        from ada.fem.formats.sesam.results.sets import role_groups

        extra = role_groups(result.mesh, self._elem_shown)
        if extra:
            self._groups = [*(self._groups or []), *extra]

    def _loaded(self):
        """The result with its fields read (reading them now, once, if they were deferred)."""
        if self._load_results is not None:
            load, self._load_results = self._load_results, None
            self._on_close = None  # the load owns (and releases) what it reads from
            load()
        return self._result

    def _shown_elem(self, elem_id: int) -> int:
        """The number a person reads for element ``elem_id``."""
        if self._elem_shown is None:
            return int(elem_id)
        return self._elem_shown.get(int(elem_id), int(elem_id))

    def try_fem_concepts(self) -> dict | None:
        return self._fem_concepts

    def try_groups(self) -> list[dict] | None:
        return self._groups

    def try_element_properties(self) -> dict | None:
        """What each element IS, for the viewer's Selected Object panel.

        ``labels`` (shown numbers), ``eltypes`` (the source's element type code, 0 when
        it has no integer one), ``materials`` / ``sections`` (keys into the tables
        below, 0 = none), ``thickness`` (shell thickness, NaN = none), aligned; and
        ``tables``: ``materials`` / ``sections`` ``{key: properties}`` and
        ``element_types`` ``{code: name}``. ``None`` without per-element data.
        """
        from ada.comms.msg_handling.object_metadata import (
            material_to_dict,
            section_to_dict,
        )
        from ada.fem.formats.sesam.results.property_fields import THICKNESS_FIELD

        mesh = self._result.mesh
        if mesh.elem_data is None or not len(mesh.elem_data):
            return None

        ids, eltypes = [], []
        for block in mesh.elements:
            source_type = getattr(block.elem_info, "source_type", None)
            code = int(source_type) if isinstance(source_type, (int, np.integer)) else 0
            block_ids = np.asarray(block.identifiers, dtype=np.int64)
            ids.append(block_ids)
            eltypes.append(np.full(block_ids.size, code, dtype=np.int64))
        if not ids:
            return None
        ids = np.concatenate(ids)
        eltypes = np.concatenate(eltypes)

        data = np.asarray(mesh.elem_data, dtype=float)
        row_of = {int(e): i for i, e in enumerate(data[:, 0])}
        rows = np.array([row_of.get(int(e), -1) for e in ids], dtype=np.int64)
        has = rows >= 0
        sections_by_id = mesh.sections or {}
        materials_by_id = mesh.materials or {}
        mat = np.zeros(ids.size, dtype=np.int64)
        sec = np.zeros(ids.size, dtype=np.int64)
        mat[has] = data[rows[has], 1].astype(np.int64)
        sec[has] = data[rows[has], 2].astype(np.int64)
        # A shell's geono names its thickness record, not a beam section.
        sec[~np.isin(sec, np.fromiter(sections_by_id.keys(), dtype=np.int64, count=len(sections_by_id)))] = 0
        mat[~np.isin(mat, np.fromiter(materials_by_id.keys(), dtype=np.int64, count=len(materials_by_id)))] = 0

        thickness = np.full(ids.size, np.nan, dtype=np.float64)
        for field in self._loaded().results or []:
            if getattr(field, "name", None) != THICKNESS_FIELD:
                continue
            values = np.asarray(field.values, dtype=float)
            if values.ndim != 2 or values.shape[1] < 3:
                continue
            # One row per (element, point); a shell's thickness is the element's.
            by_elem = dict(zip(values[:, 0].astype(np.int64).tolist(), values[:, 2].tolist()))
            pos = {int(e): i for i, e in enumerate(ids)}
            for e, th in by_elem.items():
                if e in pos:
                    thickness[pos[e]] = th

        used_sec = sorted({int(s) for s in sec if s})
        used_mat = sorted({int(m) for m in mat if m})
        tables = {
            "sections": {str(s): section_to_dict(sections_by_id[s]) for s in used_sec},
            "materials": {str(m): material_to_dict(materials_by_id[m]) for m in used_mat},
        }
        if str(getattr(self._result, "software", "")).lower().endswith("sesam"):
            from ada.fem.formats.sesam.results.read_sif import SESAM_ELTYPE_LABELS

            tables["element_types"] = {
                str(c): SESAM_ELTYPE_LABELS[c] for c in sorted({int(t) for t in eltypes}) if c in SESAM_ELTYPE_LABELS
            }
        return {
            "labels": np.array([self._shown_elem(int(e)) for e in ids], dtype=np.int64),
            "eltypes": eltypes,
            "materials": mat,
            "sections": sec,
            "thickness": thickness,
            "tables": tables,
        }

    def try_suppressed_line_edges(self) -> np.ndarray | None:
        """Node-index pairs of the line elements whose node-to-node line the viewer must not draw.

        GeniE's dummy-mass beams (the mesh's ``dummy_mass`` sets): node to node, each
        runs from a footprint node to the one COG node, while the element itself is
        a short stub off the footprint node and the rest a rigid offset. Their beam
        solids draw the stub; a line between the nodes would draw a web that is not
        in the model.
        """
        from ada.fem.sets import SetRoles

        mesh = self._result.mesh
        hidden: set[int] = set()
        for fs in (getattr(mesh, "sets", None) or {}).values():
            if getattr(fs, "role", None) == SetRoles.DUMMY_MASS:
                hidden.update(int(getattr(m, "id", m)) for m in fs.members)
        if not hidden:
            return None
        pairs = []
        for block in mesh.elements:
            refs = np.asarray(block.node_refs)
            if refs.ndim != 2 or refs.shape[1] < 2:
                continue
            rows = np.flatnonzero(np.isin(np.asarray(block.identifiers, dtype=np.int64), list(hidden)))
            for row in rows:
                ends = (refs[row, 0], refs[row, -1])
                if block.node_refs_are_indices:
                    pairs.append([int(ends[0]), int(ends[1])])
                elif int(ends[0]) in self._nmap and int(ends[1]) in self._nmap:
                    pairs.append([self._nmap[int(ends[0])], self._nmap[int(ends[1])]])
        return np.asarray(pairs, dtype=np.uint32).reshape(-1, 2) if pairs else None

    def try_step_names(self) -> dict[int, str] | None:
        return self._step_names

    def try_result_cases(self) -> list[dict] | None:
        return self._result_cases

    def _sesam_superseded_raw_fields(self) -> set[tuple[str, object | None]]:
        """Raw Sesam fields replaced by semantic derived fields in this result.

        The eager result intentionally retains its source-card fields for API
        compatibility and diagnostics.  They should not also be advertised by
        the viewer bake when a semantic field covers the same support, because
        that creates duplicate flat entries beside the hierarchy.

        The element type is part of the key so an unsupported shell/beam layout
        keeps its raw fallback even when another type in the same result was
        converted successfully.
        """

        software = getattr(self._result, "software", "")
        if getattr(software, "value", software) != "sesam":
            return set()

        results = self._loaded().results
        hidden: set[tuple[str, object | None]] = set()
        if any(
            getattr(getattr(field, "presentation", None), "group_path", ()) == ("Nodes", "DISPLACEMENT")
            for field in results
        ):
            hidden.add(("RVNODDIS", None))

        replacements = {
            "STRESS": {"G-STRESS", "P-STRESS", "PM-STRESS", "D-STRESS", "R-STRESS"},
            "FORCES": {"G-FORCE", "B-STRESS"},
        }
        for raw_name, attributes in replacements.items():
            covered_types = {
                getattr(field, "elem_type", None)
                for field in results
                if getattr(getattr(field, "presentation", None), "group_path", ())[-1:]
                and field.presentation.group_path[-1] in attributes
            }
            hidden.update((raw_name, elem_type) for elem_type in covered_types)
        return hidden

    # ----- protocol -------------------------------------------------------

    def read_mesh_geometry(self) -> MeshGeometry:
        if self._geom is not None:
            return self._geom

        from ada.fem.shapes.mesh_types import ada_to_str_type

        points = np.asarray(self._result.mesh.nodes.coords, dtype=np.float64)
        if points.ndim == 2 and points.shape[1] == 2:
            points = np.column_stack([points, np.zeros(points.shape[0])])

        cell_blocks: list[CellBlockData] = []
        for block in self._result.mesh.elements:
            cell_type_str = ada_to_str_type.get(block.elem_info.type)
            if cell_type_str is None:
                # Unsupported element type for visualisation — skip
                # rather than crash, matching the legacy GLB pipeline's
                # posture. Mesh GLB just gets fewer faces.
                continue

            flat = np.asarray(block.node_refs).reshape(-1)
            try:
                data_0 = np.fromiter(
                    (self._nmap[int(x)] for x in flat),
                    dtype=np.int64,
                    count=flat.size,
                ).reshape(block.node_refs.shape)
            except KeyError as e:
                # Element references a node that isn't in the mesh;
                # surface explicitly so the source data error is
                # obvious.
                raise ValueError(
                    f"Element block of type {cell_type_str!r} references " f"unknown node id {e.args[0]}."
                ) from None
            # ElementBlock.identifiers is the per-element label as it
            # appeared in the source FEA file. Forward verbatim so the
            # selection sidecar can carry real labels back to the
            # picker, not just iteration-order indices -- the number
            # people read, where the block keeps one apart (labels).
            block_ids = getattr(block, "labels", None)
            if block_ids is None:
                block_ids = getattr(block, "identifiers", None)
            if block_ids is not None:
                identifiers = np.asarray(block_ids, dtype=np.int64).reshape(-1)
                if identifiers.shape[0] != data_0.shape[0]:
                    # Defensive: if a reader produces a length mismatch
                    # we'd silently misattribute labels; surface it.
                    raise ValueError(
                        f"ElementBlock.identifiers length {identifiers.shape[0]} "
                        f"!= n_cells {data_0.shape[0]} for {cell_type_str!r}."
                    )
            else:
                identifiers = None
            cell_blocks.append(CellBlockData(cell_type=cell_type_str, data=data_0, identifiers=identifiers))

        mesh_nodes = self._result.mesh.nodes
        shown_nodes = getattr(mesh_nodes, "labels", None)
        node_labels = [int(x) for x in (mesh_nodes.identifiers if shown_nodes is None else shown_nodes)]
        self._geom = MeshGeometry(points=points, cell_blocks=cell_blocks, node_labels=node_labels)
        return self._geom

    def _named_case_analysis_kind(self) -> str | None:
        """``"static"`` when the deck defines load-case combinations.

        ``_infer_analysis_kind`` reads ``step_values`` as a physical quantity —
        eigen frequencies or times — and calls a run "eigen" when they are
        positive and ascending. For a Sesam deck those values are result-case
        NUMBERS (1, 2, 3 …), which are positive and ascending by definition, so
        every nodal field on a ten-load-case deck came out as a mode shape.

        Element fields never showed it because their branch defaults an unknown
        kind to "static" rather than guessing, which is why the two halves of
        one manifest disagreed with each other.

        The conclusive evidence is a real-valued ``RDRESCMB`` COMBINATION: a
        recipe that superposes load cases at factors. A modal analysis has no
        such thing. Named cases alone are not evidence -- a SESTRA eigen run
        labels its modes too, and treating those as static would break the
        signed deformation sweep a mode shape needs. Returns ``None`` without
        that evidence, leaving the heuristic to decide as before.
        """
        return analysis_kind_from_result_cases(self._result_cases)

    def _result_analysis_kind(self, results: list) -> str | None:
        """``"eigen"`` / ``"static"`` from the result itself, when it says.

        A mode's field carries its eigen frequency; a result with no eigen data at all is not a
        modal one. Without this the step-value heuristic decided, and it reads ONE positive step
        as a mode: an Abaqus static step ends at time 1.0 and a Sesam load case is number 1, so
        their static deflections were baked as eigenmodes -- normalized to a tenth of the model
        (2.3x too large on the verification plate strip) and swept through +/- as an oscillation.
        ``None`` when neither holds, which leaves the heuristic to decide as before.
        """
        explicit = getattr(self._result, "analysis_kind", None)
        if explicit is not None:
            return explicit
        if any(getattr(r, "eigen_freq", None) is not None for r in results):
            return "eigen"
        if getattr(self._result, "eigen_mode_data", None) is None:
            return "static"
        return None

    def field_specs(self) -> list[FieldSpec]:
        if self._field_specs is not None:
            return self._field_specs

        from ada.fem.results.field_data import NodalFieldData

        n_points = int(self._result.mesh.nodes.coords.shape[0])
        specs: list[FieldSpec] = []

        hidden_raw = self._sesam_superseded_raw_fields()
        for name, results in self._loaded().get_results_grouped_by_field_value().items():
            if not results:
                continue
            sorted_results = sorted(results, key=lambda r: r.step)
            first = sorted_results[0]

            if not isinstance(first, NodalFieldData):
                # Element fields are planned and streamed exclusively through
                # element_field_specs()/iter_element_field_steps(). Advertising
                # them here as AFBL fields duplicates picker entries and makes
                # callers think iter_field_steps can emit Gauss data.
                continue
            if (name, None) in hidden_raw:
                continue
            support = "nodal"

            # Step value semantics: eigen analysis stores the
            # frequency in eigen_freq and uses .step as a 1-based
            # index. Static analysis stores the time directly in
            # .step. The picker just wants a monotonic label per
            # step, so either works.
            step_values = [float(r.eigen_freq if r.eigen_freq is not None else r.step) for r in sorted_results]
            components = list(first.components) or [first.name]

            specs.append(
                FieldSpec(
                    name=name,
                    components=components,
                    n_steps=len(sorted_results),
                    n_points=n_points,
                    support=support,
                    step_values=step_values,
                    category=_classify_field(name, first),
                    presentation=getattr(first, "presentation", None),
                    analysis_kind=self._named_case_analysis_kind() or self._result_analysis_kind(sorted_results),
                    difference=bool(getattr(first, "difference", False)),
                )
            )

        self._field_specs = specs
        return specs

    def iter_field_steps(self, field_name: str):
        spec = next((s for s in self.field_specs() if s.name == field_name), None)
        if spec is None:
            raise KeyError(field_name)
        if spec.support != "nodal":
            raise NotImplementedError(
                f"streaming non-nodal field {field_name!r} (support={spec.support}) "
                f"not implemented via iter_field_steps; use iter_element_field_steps"
            )

        results = self._loaded().get_results_grouped_by_field_value().get(field_name, [])
        sorted_results = sorted(results, key=lambda r: r.step)
        for i, r in enumerate(sorted_results):
            arr = self._dense_nodal_values(r, np.asarray(r.get_all_values()), spec.n_points)
            yield StepValues(
                step_index=i,
                step_value=spec.step_values[i],
                values=arr,
            )

    def _dense_nodal_values(self, result, arr: np.ndarray, n_points: int) -> np.ndarray:
        """``arr`` (one row per row of ``result``) as one row per mesh point.

        A field normally has a row for every node, in mesh order, and is passed through as
        is. Some steps have fewer: Sestra writes no displacement record for a node without
        degrees of freedom, so one load case of a deck can list fewer nodes than another.
        Those rows are placed by their node id (column 0 of a nodal field). A node with no
        row gets zero displacement -- it has no degree of freedom to move -- and, for any
        other field, NaN, which the viewer shows as "no value"."""
        if arr.shape[0] == n_points:
            return arr
        from ada.fem.results.field_data import NodalFieldType

        node_ids = np.asarray(result.values, dtype=float)[:, 0].astype(np.int64)
        rows = np.array([self._nmap.get(int(n), -1) for n in node_ids], dtype=np.int64)
        known = rows >= 0
        fill = 0.0 if getattr(result, "field_type", None) == NodalFieldType.DISP else np.nan
        out = np.full((n_points,) + arr.shape[1:], fill, dtype=np.result_type(arr.dtype, np.float32))
        out[rows[known]] = arr[known]
        return out

    def _grouped_element_fields(self):
        """Group ``ElementFieldData`` rows by ``(name, elem_type)``.

        Cached as ``self._elem_field_groups`` so callers (specs +
        iterators) walk the FEAResult.results list once. Skip
        ElementFieldData with an unknown elem_type — the GLB has no
        geometry for it so colouring it would have nowhere to land."""

        from ada.fem.results.field_data import ElementFieldData
        from ada.fem.shapes.mesh_types import ada_to_str_type

        cached = getattr(self, "_elem_field_groups", None)
        if cached is not None:
            return cached

        grouped: dict[tuple[str, str], list] = defaultdict(list)
        hidden_raw = self._sesam_superseded_raw_fields()
        for r in self._loaded().results:
            if not isinstance(r, ElementFieldData):
                continue
            if r.elem_type is None:
                continue
            elem_type_str = ada_to_str_type.get(r.elem_type)
            if elem_type_str is None:
                continue
            if (r.name, r.elem_type) in hidden_raw:
                continue
            grouped[(r.name, elem_type_str)].append(r)

        self._elem_field_groups = grouped
        return grouped

    def element_field_specs(self) -> list[ElementFieldSpec]:
        if self._elem_field_specs is not None:
            return self._elem_field_specs

        from ada.fem.results.field_data import FieldPosition

        specs: list[ElementFieldSpec] = []
        node_index = {int(label): i for i, label in enumerate(self._result.mesh.nodes.identifiers)}
        element_nodes: dict[int, list[int]] = {}
        for block in self._result.mesh.elements:
            for label, refs in zip(block.identifiers, block.node_refs):
                element_nodes[int(label)] = [node_index[int(ref)] for ref in refs]
        for (name, elem_type_str), items in self._grouped_element_fields().items():
            sorted_items = sorted(items, key=lambda r: r.step)
            first = sorted_items[0]
            vals = np.asarray(first.values)
            if vals.ndim != 2 or vals.shape[1] < 2 + len(first.components):
                # Row layout is (elem_label, ip_index, *component_values);
                # surface the shape mismatch rather than silently mis-baking.
                raise ValueError(
                    f"element field {name!r} ({elem_type_str}) has unexpected "
                    f"values shape {vals.shape}; expected (n_rows, "
                    f">= 2 + {len(first.components)})."
                )

            ip_indices = vals[:, 1].astype(int)
            if ip_indices.size == 0:
                continue
            n_ips = int(ip_indices.max())
            if n_ips <= 0:
                # IP indices are 1-based in the SIF reader; a non-positive
                # max means the source data is malformed for this field.
                raise ValueError(
                    f"element field {name!r} ({elem_type_str}) has non-positive " f"IP indices; cannot determine n_ips."
                )
            if vals.shape[0] % n_ips != 0:
                raise ValueError(
                    f"element field {name!r} ({elem_type_str}) row count "
                    f"{vals.shape[0]} is not a multiple of n_ips={n_ips}; "
                    f"likely a ragged IP layout the bake doesn't yet handle."
                )
            # Element labels appear once per element (we stride by n_ips
            # through col 0). Row order from the reader becomes the
            # spec's canonical order — manifest carries it so the
            # frontend can map ``label → bucket index``. A later step may
            # list elements the first does not (or omit some: a load case
            # that leaves part of the model unloaded writes no forces there),
            # so the canonical list is every element any step lists, the
            # first step's in its order and the others' after them.
            labels = vals[::n_ips, 0].astype(int).tolist()
            seen = set(labels)
            for later in sorted_items[1:]:
                later_vals = np.asarray(later.values)
                if later_vals.ndim != 2 or later_vals.shape[0] % n_ips:
                    continue  # iter_element_field_steps reports it
                for label in later_vals[::n_ips, 0].astype(int).tolist():
                    if label not in seen:
                        seen.add(label)
                        labels.append(label)
            self._elem_field_labels[(name, elem_type_str)] = labels
            n_elements = len(labels)

            step_values = [float(r.eigen_freq if r.eigen_freq is not None else r.step) for r in sorted_items]
            ip_layout = _ip_layout_from_int_positions(getattr(first, "int_positions", None))

            specs.append(
                ElementFieldSpec(
                    name=name,
                    components=list(first.components),
                    n_steps=len(sorted_items),
                    elem_type=elem_type_str,
                    n_elements=n_elements,
                    n_ips=n_ips,
                    element_labels=[self._shown_elem(label) for label in labels],
                    step_values=step_values,
                    element_node_indices=[element_nodes.get(int(label), []) for label in labels],
                    ip_layout=ip_layout,
                    category=_classify_field(name, first),
                    analysis_kind=getattr(self._result, "analysis_kind", None),
                    support={
                        FieldPosition.ELEMENT_NODAL: "element_nodal",
                        FieldPosition.ELEMENT_AVERAGE: "element_average",
                        FieldPosition.RESULT_POINT: "result_point",
                        FieldPosition.LINE_RESULT_POINT: "line_result_point",
                    }.get(first.field_pos, "gauss"),
                    presentation=getattr(first, "presentation", None),
                    difference=bool(getattr(first, "difference", False)),
                )
            )

        self._elem_field_specs = specs
        return specs

    def iter_element_field_steps(self, spec: ElementFieldSpec):
        from ada.fem.results.field_data import ElementFieldData  # noqa: F401

        items = self._grouped_element_fields().get((spec.name, spec.elem_type))
        if not items:
            raise KeyError((spec.name, spec.elem_type))
        sorted_items = sorted(items, key=lambda r: r.step)
        if len(sorted_items) != spec.n_steps:
            raise ValueError(
                f"element field {spec.name!r} step-count drift: spec says "
                f"{spec.n_steps}, found {len(sorted_items)}."
            )

        n_components = len(spec.components)
        if (spec.name, spec.elem_type) not in self._elem_field_labels:
            self._elem_field_specs = None
            self.element_field_specs()
        labels = self._elem_field_labels[(spec.name, spec.elem_type)]
        position = None
        for i, r in enumerate(sorted_items):
            # Not float32 yet: column 0 is the element id, and float32 holds integers exactly
            # only up to 2**24 -- element 21000001 would come back as 21000000. The components
            # are cast below, the ids never.
            vals = np.asarray(r.values, dtype=np.float64)
            if vals.ndim != 2 or vals.shape[0] % spec.n_ips:
                raise ValueError(
                    f"element field {spec.name!r} step {r.step} has shape "
                    f"{vals.shape}; expected a multiple of n_ips={spec.n_ips} rows."
                )
            per_elem = vals.reshape(-1, spec.n_ips, vals.shape[1])
            step_labels = per_elem[:, 0, 0].astype(np.int64)
            # First 2 columns are (elem_label, ip_index); strip them.
            if per_elem.shape[0] == spec.n_elements and np.array_equal(step_labels, labels):
                comp_vals = per_elem[:, :, 2 : 2 + n_components]
            else:
                # This step lists other elements, or the same ones in another order:
                # placed by label, and an element it does not list has no value.
                if position is None:
                    position = {label: k for k, label in enumerate(labels)}
                rows = np.array([position[int(label)] for label in step_labels], dtype=np.int64)
                comp_vals = np.full((spec.n_elements, spec.n_ips, n_components), np.nan, dtype=np.float32)
                comp_vals[rows] = per_elem[:, :, 2 : 2 + n_components]
            yield ElementStepValues(
                step_index=i,
                step_value=spec.step_values[i],
                values=np.ascontiguousarray(comp_vals, dtype=np.float32),
            )

    def try_solid_beams(
        self,
        *,
        method: str = "procedural",
        format: str = "mesh",
    ) -> SolidBeamMesh | BeamSolidInstances | None:
        """Tessellate each beam (line) element as a 3D extruded section
        and merge into a single vertex+index buffer with per-beam draw
        ranges. ``method`` picks the extruder — ``"procedural"`` (numpy)
        or ``"occ"`` (the CAD kernel); see
        :func:`tessellate_beams_to_solid_mesh`.

        ``format="compact"`` stops short of the buffers and returns the
        generators instead — a :class:`~.beam_compact.BeamSolidInstances`
        the viewer expands into the same mesh. Everything above the
        dispatch at the bottom (which beams qualify, their eccentricity
        correction, the pre-filter buckets) is shared, so the two formats
        cannot disagree about which beams are in the model or where they
        sit.

        Requires the wrapped FEAResult.mesh to carry sections +
        materials + vectors + elem_data (the SIF reader populates all
        four; RMED native does not). Returns ``None`` when any of
        those is missing — the bake then skips solid-beam emission.

        Individual beam tessellation failures (bad section, OCC blow-
        up) are logged and the offending beam is omitted from the
        output rather than failing the whole bake. Empty result →
        return ``None`` so the manifest doesn't carry a zero-element
        sidecar.
        """

        mesh = self._result.mesh
        if not getattr(mesh, "sections", None):
            return None
        if mesh.elem_data is None:
            return None

        from ada import Beam, Part
        from ada.fem.formats.utils import line_elem_to_beam
        from ada.fem.results.beam_placement import SectionCentroidCache, eccentric_shift

        line_elems = mesh.get_line_elems()
        if not line_elems:
            return None

        dummy_part = Part(self._result.name or "solid_beams")
        beams: list = []
        # Eccentricities, corrected for where adapy actually draws each profile.
        # See beam_placement: the raw GECCEN vector positions the section CENTROID,
        # and applying it directly moves L sections off plates they are already
        # flush against.
        eccentricities = getattr(mesh, "eccentricities", None) or {}
        centroids = SectionCentroidCache()
        # Source-side pre-filter — these reject reasons are cheap to
        # detect before OCC sees the geometry. Bucketing them by
        # category here keeps the bake's coverage summary informative.
        extra_skip: dict[str, int] = {}
        for elem in line_elems:
            n0_node = elem.nodes[0]
            n1_node = elem.nodes[-1]
            try:
                n0_idx = self._nmap[int(n0_node.id)]
                n1_idx = self._nmap[int(n1_node.id)]
            except KeyError:
                extra_skip["endpoint-not-in-mesh"] = extra_skip.get("endpoint-not-in-mesh", 0) + 1
                continue

            sec = elem.fem_sec.section if elem.fem_sec is not None else None
            if sec is None:
                extra_skip["no-section"] = extra_skip.get("no-section", 0) + 1
                continue
            if getattr(sec, "type", None) == "GENBEAM":
                # Generic-cross-section beams carry property numbers
                # only (A, Iy, Iz, …) — no geometric profile to
                # extrude. Most common gap in Sesam models.
                extra_skip["genbeam-no-profile"] = extra_skip.get("genbeam-no-profile", 0) + 1
                continue
            if elem.fem_sec.local_z is None:
                extra_skip["missing-local-z"] = extra_skip.get("missing-local-z", 0) + 1
                continue

            # The element's own end points: node + GECCEN (SIF 7.3.10, the vector runs
            # from the node to the element end). Sesam's local x-axis runs between THESE,
            # not between the nodes, and GUNIVEC's local z is given against that axis.
            # Built on the node axis instead, a beam whose offsets are not equal at both
            # ends gets the wrong axis, and a GUNIVEC that is perpendicular to the true
            # axis is not perpendicular to it -- GeniE's dummy-mass beams (a 1 m vertical
            # stub off each plate node, offset to one COG node) drew as long, tilted tubes.
            ecc = eccentricities.get(int(elem.id))
            p0 = np.asarray(n0_node.p, dtype=float)
            p1 = np.asarray(n1_node.p, dtype=float)
            if ecc:
                if ecc[0] is not None:
                    p0 = p0 + np.asarray(ecc[0], dtype=float)
                if ecc[-1] is not None:
                    p1 = p1 + np.asarray(ecc[-1], dtype=float)
            if not np.any(p1 != p0):
                # Both ends on one point (a coupling link modelled as a beam): no axis
                # to extrude the profile along.
                extra_skip["zero-length"] = extra_skip.get("zero-length", 0) + 1
                continue

            if ecc:
                beam = Beam(
                    f"BM{elem.id}",
                    p0,
                    p1,
                    sec=sec,
                    mat=elem.fem_sec.material,
                    up=elem.fem_sec.local_z,
                    parent=dummy_part,
                )
                # The axis already carries the eccentricity, so what is left is where
                # adapy draws the profile against its centroid (beam_placement): the
                # shift for a zero eccentricity. Only an end with an offset gets it,
                # which is what a half-eccentric element means.
                correction = eccentric_shift(beam, np.zeros(3), centroids)
                shift0 = correction if ecc[0] is not None else None
                shift1 = correction if ecc[-1] is not None else None
                # NEGATED on the way in. Beam.e1/e2 are not applied as written:
                # BeamJustification.curve_offset_local does `off = -e` ("local
                # offsets start from -e"), so handing it the geometric translation
                # places the profile on the wrong side. For a section that is
                # symmetric in EXTENT that still lands a face on the plate, which
                # is why a flush check alone did not catch it -- it put the wide
                # flange against the plate and the web tip out in the air.
                if shift0 is not None:
                    beam.e1 = -shift0
                if shift1 is not None:
                    beam.e2 = -shift1
            else:
                beam = line_elem_to_beam(elem, dummy_part, "BM")

            # Labelled with the number people read, like the mesh sidecar's elements.
            beams.append((beam, self._shown_elem(elem.id), n0_idx, n1_idx, n0_node.p, n1_node.p))

        if format == "compact":
            from .beam_compact import collect_beam_solid_instances

            return collect_beam_solid_instances(
                beams,
                extra_skip_reasons=extra_skip,
                total_beams=len(line_elems),
            )

        return tessellate_beams_to_solid_mesh(
            beams,
            extra_skip_reasons=extra_skip,
            total_beams=len(line_elems),
            method=method,
        )

    def close(self) -> None:
        if self._on_close is not None:
            on_close, self._on_close = self._on_close, None
            self._load_results = None
            on_close()

    def __enter__(self) -> "FEAResultStreamAdapter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

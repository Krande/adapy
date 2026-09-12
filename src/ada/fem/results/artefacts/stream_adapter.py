"""Adapt an in-memory FEAResult to the streaming reader protocol."""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ada.fem.results.common import CellBlockData

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

    def __init__(self, result):
        self._result = result
        self._geom: MeshGeometry | None = None
        self._field_specs: list[FieldSpec] | None = None
        self._elem_field_specs: list[ElementFieldSpec] | None = None
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

    def try_fem_concepts(self) -> dict | None:
        return self._fem_concepts

    def try_groups(self) -> list[dict] | None:
        return self._groups

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

        results = self._result.results
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
            # picker, not just iteration-order indices.
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

        node_labels = [int(x) for x in self._result.mesh.nodes.identifiers]
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

    def field_specs(self) -> list[FieldSpec]:
        if self._field_specs is not None:
            return self._field_specs

        from ada.fem.results.field_data import NodalFieldData

        n_points = int(self._result.mesh.nodes.coords.shape[0])
        specs: list[FieldSpec] = []

        hidden_raw = self._sesam_superseded_raw_fields()
        for name, results in self._result.get_results_grouped_by_field_value().items():
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
                    analysis_kind=self._named_case_analysis_kind(),
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

        results = self._result.get_results_grouped_by_field_value().get(field_name, [])
        sorted_results = sorted(results, key=lambda r: r.step)
        for i, r in enumerate(sorted_results):
            arr = np.asarray(r.get_all_values())
            yield StepValues(
                step_index=i,
                step_value=spec.step_values[i],
                values=arr,
            )

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
        for r in self._result.results:
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
            n_elements = vals.shape[0] // n_ips
            # Element labels appear once per element (we stride by n_ips
            # through col 0). Row order from the reader becomes the
            # spec's canonical order — manifest carries it so the
            # frontend can map ``label → bucket index``.
            labels = vals[::n_ips, 0].astype(int).tolist()

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
                    element_labels=labels,
                    step_values=step_values,
                    element_node_indices=[element_nodes.get(int(label), []) for label in labels],
                    ip_layout=ip_layout,
                    category=_classify_field(name, first),
                    support={
                        FieldPosition.ELEMENT_NODAL: "element_nodal",
                        FieldPosition.ELEMENT_AVERAGE: "element_average",
                        FieldPosition.RESULT_POINT: "result_point",
                        FieldPosition.LINE_RESULT_POINT: "line_result_point",
                    }.get(first.field_pos, "gauss"),
                    presentation=getattr(first, "presentation", None),
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
        for i, r in enumerate(sorted_items):
            vals = np.asarray(r.values, dtype=np.float32)
            if vals.shape != (spec.n_elements * spec.n_ips, vals.shape[1]):
                raise ValueError(
                    f"element field {spec.name!r} step {r.step} has shape "
                    f"{vals.shape}; expected ({spec.n_elements * spec.n_ips}, ...)."
                )
            per_elem = vals.reshape(spec.n_elements, spec.n_ips, -1)
            # First 2 columns are (elem_label, ip_index); strip them.
            comp_vals = per_elem[:, :, 2 : 2 + n_components]
            # Verify label alignment with the spec's canonical order —
            # readers that emit rows in different orders between steps
            # would silently mis-correlate.
            step_labels = per_elem[:, 0, 0].astype(int).tolist()
            if step_labels != spec.element_labels:
                raise ValueError(
                    f"element field {spec.name!r} step {r.step} label order "
                    f"differs from spec; reader yielded different element "
                    f"order between steps."
                )
            yield ElementStepValues(
                step_index=i,
                step_value=spec.step_values[i],
                values=np.ascontiguousarray(comp_vals, dtype=np.float32),
            )

    def try_solid_beams(self) -> "SolidBeamMesh | None":
        """Tessellate each beam (line) element as a 3D extruded section
        via OCC and merge into a single vertex+index buffer with
        per-beam draw ranges.

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

        from ada import Part
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

            beam = line_elem_to_beam(elem, dummy_part, "BM")

            ecc = eccentricities.get(int(elem.id))
            if ecc:
                # Both ends get the same correction when both carry an offset; an
                # end without one is left on its node, which is what a half-eccentric
                # element means.
                shift0 = eccentric_shift(beam, ecc[0], centroids) if ecc[0] is not None else None
                shift1 = eccentric_shift(beam, ecc[-1], centroids) if ecc[-1] is not None else None
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

            beams.append((beam, int(elem.id), n0_idx, n1_idx, n0_node.p, n1_node.p))

        return tessellate_beams_to_solid_mesh(
            beams,
            extra_skip_reasons=extra_skip,
            total_beams=len(line_elems),
        )

    def close(self) -> None:
        pass

    def __enter__(self) -> "FEAResultStreamAdapter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

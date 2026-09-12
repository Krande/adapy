"""Manifest builder and writer."""

from __future__ import annotations

import json
import os
import pathlib
from collections import defaultdict

from ada.fem.results.field_data import FieldPresentation

from .formats import (
    BLOB_HEADER_BYTES,
    ELEM_FIELD_HEADER_BYTES,
    FEA_BAKE_VERSION,
    MANIFEST_VERSION,
)
from .history import HistoryRecords, build_history_payload
from .specs import ElementFieldArtefactMeta, FieldArtefactMeta, FieldSpec, MeshGeometry

# ---------------------------------------------------------------------------
# Manifest writer
# ---------------------------------------------------------------------------


def _default_view_for(spec: FieldSpec) -> dict:
    """Pick the picker's initial state for a field. Vector fields default
    to magnitude reduction; scalars default to the field itself. All
    fields use viridis."""

    return {
        "reduction": (
            spec.components[0]
            if spec.presentation is not None and spec.components
            else ("magnitude" if spec.n_components >= 3 else "scalar")
        ),
        "colormap": "viridis",
    }


def _value_label_key(value: float) -> str:
    """JSON key for a labelled value, matching JavaScript's ``String(number)``:
    an integral id prints as ``"3"``, never ``"3.0"`` — the frontend looks the
    stored value up by exactly that string."""

    v = float(value)
    return str(int(v)) if v.is_integer() else str(v)


def _presentation_payload(presentation: FieldPresentation | None) -> dict:
    if presentation is None:
        return {}
    return {
        "semantic_key": presentation.semantic_key,
        "group_path": list(presentation.group_path),
        "coordinate_system": presentation.coordinate_system,
        "surface": presentation.surface,
        "derived": bool(presentation.derived),
        "unit": presentation.unit,
        "component_units": list(presentation.component_units),
        **(
            {"value_labels": {_value_label_key(value): label for value, label in presentation.value_labels}}
            if presentation.value_labels
            else {}
        ),
    }


def analysis_kind_from_result_cases(cases) -> str | None:
    """``"static"`` when a deck defines load-case combinations, else ``None``.

    Split out from the adapter so the decision can be tested without building a
    whole ``FEAResult``. See ``_named_case_analysis_kind`` for why it matters.
    """
    for case in cases or ():
        if isinstance(case, dict) and case.get("combination"):
            return "static"
    return None


def _infer_analysis_kind(spec: FieldSpec) -> str:
    """Infer 'static' vs 'eigen' from a field's step value sequence.

    The bake doesn't carry the original analysis-type flag, but the
    streaming readers populate ``step_values`` with eigen frequencies
    for modal output (monotonically increasing positives) and time
    values for transient/static (typically starts at zero, may be a
    single step). One eigen tell: a typical mode shape produces a
    single field with multiple steps where the first value is
    strictly positive and unique. A static analysis with multiple
    steps starts at t=0. Single-step + zero-time → static.

    Picker drives the deformation-scale slider range from this:
    static = [0, 1] (displacement is one-directional, signed sweep
    isn't physical), eigen = [-1, +1] (mode shape has no inherent
    sign).
    """

    if spec.analysis_kind is not None:
        return spec.analysis_kind
    if spec.n_steps == 0:
        return "static"
    first = float(spec.step_values[0])
    # Eigen analyses produce strictly positive frequencies starting
    # from a non-zero value; static/transient runs almost always
    # start at t=0.
    if first > 0.0 and spec.n_steps >= 1:
        # Single-step at non-zero might still be static at a finite
        # time, but the conservative call is "treat as eigen" only
        # when we have a clear modal signature: multi-step ascending
        # positives.
        if spec.n_steps >= 2:
            ascending = all(spec.step_values[i + 1] > spec.step_values[i] for i in range(spec.n_steps - 1))
            if ascending:
                return "eigen"
        else:
            return "eigen"
    return "static"


def build_manifest(
    src: str,
    mesh_geom: MeshGeometry,
    mesh_glb_filename: str,
    field_metas: list[FieldArtefactMeta],
    *,
    source_sha256: str | None = None,
    elem_field_metas: list[ElementFieldArtefactMeta] | None = None,
    mesh_edges_filename: str | None = None,
    mesh_line_edges_filename: str | None = None,
    n_edges: int = 0,
    beam_solids_glb_filename: str | None = None,
    beam_solids_elements_filename: str | None = None,
    beam_solids_warp_filename: str | None = None,
    n_beam_solids: int = 0,
    n_beam_solid_verts: int = 0,
    n_beam_total: int = 0,
    beam_solids_skip_reasons: dict | None = None,
    mesh_elements_filename: str | None = None,
    n_elements: int = 0,
    history: "HistoryRecords | None" = None,
    lineage: dict | None = None,
    fem_concepts: dict | None = None,
    groups: list[dict] | None = None,
    step_names: dict[int, str] | None = None,
    result_cases: list[dict] | None = None,
    legacy_glb_url_template: str | None = None,
) -> dict:
    """Compose the manifest dict from the bake outputs.

    Element-field metas are grouped by ``spec.name`` so a single
    logical field (e.g. ``STRESS``) carries multiple ``per_type``
    buckets — one per element type the source ships with.

    ``lineage`` (optional) carries the CAD↔FEA back-reference that
    adapy's writers stamp into format-specific sidecars (currently
    the code_aster ``<name>.adapy_fem.json``). Shape:
    ``{"assembly_guid": str, "groups": [{"parent_object_guid": str,
    "parent_object_name": str, "members": ["E17", ...]}]}``.
    Frontend feeds this to ``useLineageStore`` so a click in the FEA
    viewer can jump to the parent beam in a loaded CAD overlay.

    ``fem_concepts`` (optional) carries the FEA *input* concepts —
    point masses, boundary conditions, and per-case / combination load
    scenarios — read back from the same deck-write sidecar (the .rmed
    result has none of them). Same shape as the ``fem_concepts``
    glTF-extension block; the frontend renders it via the shared
    FemConceptsController overlay in the viewer's FEM mode."""

    n_cells = sum(int(cb.data.shape[0]) for cb in mesh_geom.cell_blocks)
    fields_payload = []
    for fm in field_metas:
        spec = fm.spec
        scalar_range = {**fm.scalar_range_per_component}
        # Every non-scalar kind ("vector2" included) must carry a magnitude
        # range: the manifest contract promises one for kind "vector*".
        if spec.n_components >= 2:
            scalar_range["magnitude"] = list(fm.scalar_range_magnitude)
        # Convert tuple → list for JSON-friendly shape.
        scalar_range = {k: list(v) for k, v in scalar_range.items()}

        steps = [_step_entry(i, v, _format_step_label(spec, i, v), step_names) for i, v in enumerate(spec.step_values)]

        fields_payload.append(
            {
                "name_canonical": spec.name,
                "name_native": spec.name,
                "kind": spec.kind,
                "category": spec.category,
                "support": spec.support,
                "analysis_kind": _infer_analysis_kind(spec),
                "components": spec.components,
                "blob": {
                    "url": fm.blob_filename,
                    "header_bytes": BLOB_HEADER_BYTES,
                    "stride_bytes": fm.stride_bytes,
                    "dtype": spec.dtype.name,
                    "byte_order": "little",
                },
                "n_steps": spec.n_steps,
                "steps": steps,
                "scalar_range": scalar_range,
                "default_view": _default_view_for(spec),
                **_presentation_payload(spec.presentation),
            }
        )

    # Link separate nodal upper/lower blobs as variants of one semantic field.
    # Element-backed shell fields carry surfaces on their IP axis and therefore
    # need no duplicate field. Optional metadata keeps older manifests valid.
    semantic_variants: dict[str, list[dict]] = defaultdict(list)
    for field_payload in fields_payload:
        semantic_key = field_payload.get("semantic_key")
        surface = field_payload.get("surface")
        if semantic_key and surface in {"upper", "lower"}:
            semantic_variants[semantic_key].append({"surface": surface, "field_name": field_payload["name_canonical"]})
    for variants in semantic_variants.values():
        if len(variants) < 2:
            continue
        variants.sort(key=lambda item: 0 if item["surface"] == "upper" else 1)
        for variant in variants:
            field_payload = next(item for item in fields_payload if item["name_canonical"] == variant["field_name"])
            field_payload["surface_variants"] = variants

    # Element fields. Group by field name so STRESS on QUAD + TRI lands
    # under one manifest entry with two per_type buckets. Within a
    # logical field the bake assumes step counts + canonical step
    # values match across element types (Sesam emits parallel step
    # sets for all element types) — surface a hard error otherwise so
    # the frontend doesn't silently de-sync per-type animation.
    elem_by_name: dict[str, list[ElementFieldArtefactMeta]] = defaultdict(list)
    for em in elem_field_metas or []:
        elem_by_name[em.spec.name].append(em)
    for name, metas in elem_by_name.items():
        primary = metas[0].spec
        for em in metas[1:]:
            if em.spec.step_values != primary.step_values:
                raise ValueError(
                    f"Element field {name!r}: step_values differ between "
                    f"types ({primary.elem_type} vs {em.spec.elem_type}); "
                    f"the bake currently requires aligned step sets."
                )
            if em.spec.components != primary.components:
                raise ValueError(
                    f"Element field {name!r}: components differ between "
                    f"types ({primary.elem_type} vs {em.spec.elem_type})."
                )

        # Roll up per-component range across all per_type buckets so
        # the colour LUT stays fixed when the user switches IP / layer
        # / reduction without re-fetching.
        roll_comp: dict[str, tuple[float, float]] = {}
        roll_mag = (float("inf"), float("-inf"))
        for em in metas:
            for cname, (lo, hi) in em.scalar_range_per_component.items():
                if cname in roll_comp:
                    rlo, rhi = roll_comp[cname]
                    roll_comp[cname] = (min(rlo, lo), max(rhi, hi))
                else:
                    roll_comp[cname] = (lo, hi)
            mlo, mhi = em.scalar_range_magnitude
            roll_mag = (min(roll_mag[0], mlo), max(roll_mag[1], mhi))
        if not (roll_mag[0] != float("inf") and roll_mag[1] != float("-inf")):
            roll_mag = (0.0, 0.0)

        scalar_range_payload = {k: list(v) for k, v in roll_comp.items()}
        # Same contract as the nodal payload: kind "vector*" carries magnitude.
        if primary.n_components >= 2:
            scalar_range_payload["magnitude"] = list(roll_mag)

        steps = [
            _step_entry(i, v, _format_step_label_simple(primary.n_steps, primary.name, v), step_names)
            for i, v in enumerate(primary.step_values)
        ]

        per_type = []
        for em in metas:
            es = em.spec
            per_type.append(
                {
                    "elem_type": es.elem_type,
                    "n_elements": es.n_elements,
                    "n_ips": es.n_ips,
                    "ip_layout": es.ip_layout,
                    "element_labels": es.element_labels,
                    "element_node_indices": es.element_node_indices,
                    "blob": {
                        "url": em.blob_filename,
                        "header_bytes": ELEM_FIELD_HEADER_BYTES,
                        "stride_bytes": em.stride_bytes,
                        "dtype": es.dtype.name,
                        "byte_order": "little",
                    },
                    "scalar_range": {k: list(v) for k, v in em.scalar_range_per_component.items()},
                }
            )

        # Synthesise a kind so the frontend's existing
        # ``kind.startsWith("vector")`` checks treat 3-component
        # element fields the same as 3-component nodal vectors. Element
        # fields don't have a "displacement / mode shape" axis, so the
        # analysis_kind tracker just falls back to "static".
        n_comp = len(primary.components)
        if n_comp == 1:
            kind = "scalar"
        elif n_comp == 3:
            kind = "vector3"
        elif n_comp == 6:
            kind = "tensor6"
        else:
            kind = f"vector{n_comp}"

        fields_payload.append(
            {
                "name_canonical": primary.name,
                "name_native": primary.name,
                "kind": kind,
                "category": primary.category,
                "support": primary.support,
                "analysis_kind": primary.analysis_kind or "static",
                "components": primary.components,
                "n_steps": primary.n_steps,
                "steps": steps,
                "scalar_range": scalar_range_payload,
                "default_view": {
                    "reduction": (
                        primary.components[0]
                        if primary.presentation is not None and primary.components
                        else ("magnitude" if n_comp >= 3 else "scalar")
                    ),
                    "colormap": "viridis",
                    # Default layer/IP for element fields — the frontend's
                    # picker uses these as the initial dropdown values.
                    "layer": "top",
                    "ip_reduction": "max_abs",
                },
                # ``per_type`` distinguishes element fields from nodal in
                # the manifest — when present, the frontend takes the
                # AFEL render path; when absent, the legacy nodal path.
                "per_type": per_type,
                **_presentation_payload(primary.presentation),
                # A categorical field's buckets can each know different ids (the
                # materials used by shells vs by beams); the merged field must
                # label them all, not just the primary bucket's.
                **(
                    {
                        "value_labels": {
                            _value_label_key(value): label
                            for em in metas
                            if em.spec.presentation is not None
                            for value, label in em.spec.presentation.value_labels
                        }
                    }
                    if any(em.spec.presentation is not None and em.spec.presentation.value_labels for em in metas)
                    else {}
                ),
            }
        )

    mesh_meta: dict = {
        "url": mesh_glb_filename,
        "n_points": int(mesh_geom.points.shape[0]),
        "n_cells": n_cells,
    }
    # Solver node ids aligned to the points array — what a node-number label
    # prints. Omitted (not synthesised) when the reader has none: a row index
    # shown as a node number would be wrong on renumbered decks, which is
    # worse than no label. getattr, not attribute access: callers may hand in
    # any geometry-shaped object (the plugin tests do), and node_labels is the
    # optional extra here, not part of that duck type's core.
    node_labels = getattr(mesh_geom, "node_labels", None)
    if node_labels is not None:
        mesh_meta["node_labels"] = [int(x) for x in node_labels]
    if mesh_edges_filename is not None:
        mesh_meta["edges_url"] = mesh_edges_filename
        mesh_meta["n_edges"] = int(n_edges)
    if mesh_line_edges_filename is not None:
        # The subset of edges_url belonging to line elements. Optional: a model
        # with no beams omits it, and a viewer that does not know about it draws
        # edges_url whole, exactly as before.
        mesh_meta["line_edges_url"] = mesh_line_edges_filename
    if mesh_elements_filename is not None:
        mesh_meta["elements_url"] = mesh_elements_filename
        mesh_meta["n_elements"] = int(n_elements)
    if beam_solids_glb_filename is not None:
        # Parallel beam-solid mesh emitted when the reader carried
        # section + axis info per beam (SIF today). Frontend renders
        # it alongside the main mesh and can toggle between line and
        # solid display. Per-element draw ranges are keyed by the
        # line-element label so AFEL element-field colours follow.
        mesh_meta["beam_solids_url"] = beam_solids_glb_filename
        if beam_solids_elements_filename is not None:
            mesh_meta["beam_solids_elements_url"] = beam_solids_elements_filename
        if beam_solids_warp_filename is not None:
            # AFBV — per-vertex (node0_idx, node1_idx, t) mapping that
            # lets the frontend lerp nodal displacements onto the
            # solid mesh's vertices so the solid beam stays connected
            # to the rest of the structure under any morph scale.
            mesh_meta["beam_solids_warp_url"] = beam_solids_warp_filename
            mesh_meta["n_beam_solid_verts"] = int(n_beam_solid_verts)
        mesh_meta["n_beam_solids"] = int(n_beam_solids)
        # Coverage telemetry: total source-side beams + skip reasons
        # by category. Frontend can render "X of Y beams shown as
        # solids" with a tooltip listing the skipped categories so
        # users know what's missing without parsing logs.
        if n_beam_total:
            mesh_meta["n_beam_total"] = int(n_beam_total)
        if beam_solids_skip_reasons:
            mesh_meta["beam_solids_skip_reasons"] = {str(k): int(v) for k, v in beam_solids_skip_reasons.items()}

    manifest: dict = {
        "version": MANIFEST_VERSION,
        # Freshness stamp, not a format version: lets a server recognise a
        # cached bake that predates newer bake output (see FEA_BAKE_VERSION).
        "bake_version": FEA_BAKE_VERSION,
        "src": src,
        "mesh": mesh_meta,
        "fields": fields_payload,
    }
    if source_sha256:
        manifest["source_sha256"] = str(source_sha256)
    if history is not None and (history.regions or history.variables or history.series):
        manifest["history"] = build_history_payload(history)
    if lineage is not None and (lineage.get("assembly_guid") or lineage.get("groups")):
        manifest["lineage"] = lineage
    # FEA input concepts (masses / BCs / load scenarios), carried from
    # adapy's deck-write sidecar. Same shape as the ``fem_concepts``
    # glTF-extension block so the frontend renders it via the shared
    # FemConceptsController overlay.
    if fem_concepts:
        manifest["fem_concepts"] = fem_concepts
    # FEM node/element sets, for the Scene > FEM groups picker (the streaming mesh.glb carries
    # no ADA_EXT, so the frontend feeds these into useSceneInfoStore directly).
    if groups:
        manifest["groups"] = groups
    # Every result case the source OFFERS, which is more than its field steps
    # list. A "smart load combination" deck stores only its basic cases as RV*
    # records and defines the design cases as combinations of them, so a picker
    # built from steps offers the cases nobody checks and omits the ones they do.
    # Separate from `fields[].steps` rather than folded into it: a step is
    # something the colour machinery can read, and a combination is not.
    if result_cases:
        manifest["result_cases"] = result_cases
    if legacy_glb_url_template is not None:
        manifest["legacy_glb"] = {"url_template": legacy_glb_url_template}

    # Plugin artefact contributors (Decision 3). Core must NOT import any plugin;
    # instead each registered contributor is called with a small bake context and
    # its opaque return lands under the reserved ``manifest["plugins"][id]`` map.
    # Phase 1 registers no contributors, so this adds nothing (the ``plugins`` key
    # only appears when a plugin contributes) — behaviour is unchanged. Every
    # contributor is isolated so a broken plugin can't fail the bake.
    import logging as _logging

    try:
        from ada.plugins import plugin_artefact_contributors

        bake_ctx = {"src": src, "n_cells": n_cells, "n_fields": len(fields_payload)}
        for plugin_id, contribute in plugin_artefact_contributors():
            try:
                contribution = contribute(bake_ctx)
            except Exception:
                _logging.getLogger(__name__).exception("plugin %s artefact contributor failed (skipped)", plugin_id)
                continue
            if contribution is not None:
                manifest.setdefault("plugins", {})[plugin_id] = contribution
    except Exception:
        _logging.getLogger(__name__).exception("plugin artefact-contributor hook failed (non-fatal)")

    return manifest


def _step_entry(i: int, v: float, label: str, step_names: "dict[int, str] | None") -> dict:
    """One step of a field, plus the deck's name for it when there is one.

    `name` is additive and optional: every existing reader of `steps` keys off
    `i` / `value` / `label`, so a manifest without it is unchanged. Keyed on the
    step VALUE rather than the index because that is the deck's case number —
    step 0 is case 1 — and the names come from the deck.
    """
    entry = {"i": i, "value": float(v), "label": label}
    if step_names:
        name = step_names.get(int(v))
        if name:
            entry["name"] = name
    return entry


def _format_step_label(spec: FieldSpec, i: int, v: float) -> str:
    """Picker-display label per step. Single-step fields show the field
    name; multi-step fields show the step value with `:g` formatting,
    matching meshio's convention so existing fixtures keep their look."""

    return _format_step_label_simple(spec.n_steps, spec.name, v)


def _format_step_label_simple(n_steps: int, name: str, v: float) -> str:
    """FieldSpec-free variant for the element-field manifest path —
    those fields use :class:`ElementFieldSpec` which doesn't share a
    base class with :class:`FieldSpec`. Same label semantics."""

    if n_steps == 1:
        return name
    return f"{v:g}"


def write_manifest(manifest: dict, out_path: os.PathLike) -> None:
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

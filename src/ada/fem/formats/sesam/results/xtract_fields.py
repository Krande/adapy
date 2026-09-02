"""Build Xtract-style fields from raw Sesam SIN/SIF result records.

This module owns the source-specific position semantics. The frontend receives
explicit supports and never has to guess whether averaging a Gauss-point field
is equivalent to Xtract (it generally is not).

The first supported shell families are Sesam type 24/25 (four-node quad and
three-node triangle), which are the shell families in the mini_065 validation
model. Unsupported layouts retain the raw STRESS field but do not advertise
Xtract-derived fields.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.xtract_catalog import presentation, semantic_name
from ada.fem.formats.sesam.results.xtract_derived import (
    B_STRESS_COMPONENTS,
    D_STRESS_COMPONENTS,
    G_FORCE_COMPONENTS,
    G_STRESS_COMPONENTS,
    P_STRESS_COMPONENTS,
    R_STRESS_COMPONENTS,
    beam_stress,
    decompose_shell,
    general_stress,
    membrane_principal,
    opposite_section_modulus,
    plane_principal,
    stress_resultants,
)
from ada.fem.results.field_data import ElementFieldData, FieldPosition, NodalFieldData, NodalFieldType


_SHELL_CORNER_INDICES = {
    # Surface/result-point order is node0, node1, centre, node3, node2
    # for FQUS. Xtract's Elements slots follow connectivity order.
    10: (0, 1, 4, 3),
    # FTRS: node0, node1, centre, node2.
    8: (0, 1, 3),
}


def _field_position(position: str, *, line: bool = False) -> FieldPosition:
    if position == "elements":
        return FieldPosition.ELEMENT_NODAL
    if position == "element_average":
        return FieldPosition.ELEMENT_AVERAGE
    if position == "resultpoints":
        return FieldPosition.LINE_RESULT_POINT if line else FieldPosition.RESULT_POINT
    raise ValueError(position)


def _element_field(
    raw: ElementFieldData,
    position: str,
    attribute: str,
    components,
    labels: np.ndarray,
    values: np.ndarray,
    *,
    derived: bool,
    coordinate_system: str = "element_local",
    int_positions=None,
    line: bool = False,
) -> ElementFieldData:
    values = np.asarray(values, dtype=float)
    if values.ndim != 3 or values.shape[0] != len(labels):
        raise ValueError(f"{position}/{attribute}: expected (elements, slots, components), got {values.shape}")
    n_slots = values.shape[1]
    rows = np.empty((len(labels) * n_slots, 2 + values.shape[2]), dtype=float)
    rows[:, 0] = np.repeat(labels, n_slots)
    rows[:, 1] = np.tile(np.arange(1, n_slots + 1), len(labels))
    rows[:, 2:] = values.reshape(-1, values.shape[2])
    return ElementFieldData(
        name=semantic_name(position, attribute),
        step=int(raw.step),
        components=list(components),
        values=rows,
        elem_type=raw.elem_type,
        field_pos=_field_position(position, line=line),
        int_positions=int_positions or [(i, str(i)) for i in range(n_slots)],
        presentation=presentation(
            position,
            attribute,
            derived=derived,
            coordinate_system=coordinate_system,
            surface="upper" if not line else "",
        ),
    )


def _nodal_field(
    step: int,
    node_ids: np.ndarray,
    position: str,
    attribute: str,
    components,
    values: np.ndarray,
    *,
    derived: bool,
    coordinate_system: str,
    field_type: NodalFieldType = NodalFieldType.UNKNOWN,
) -> NodalFieldData:
    data = np.column_stack((node_ids, np.asarray(values, dtype=float)))
    return NodalFieldData(
        name=semantic_name(position, attribute),
        step=int(step),
        components=list(components),
        values=data,
        field_type=field_type,
        presentation=presentation(
            position,
            attribute,
            derived=derived,
            coordinate_system=coordinate_system,
            surface="upper" if "STRESS" in attribute else "",
        ),
    )


def build_xtract_nodal_kinematics(
    nodal_fields: list[NodalFieldData],
    node_ids: np.ndarray,
    *,
    wanted: set[str] | None = None,
) -> list[NodalFieldData]:
    out: list[NodalFieldData] = []
    for field in nodal_fields:
        if field.name == "RVNODDIS":
            if wanted is not None and semantic_name("nodes", "DISPLACEMENT") not in wanted:
                continue
            values = np.asarray(field.values[:, 1:7], dtype=float)
            all_translation = np.linalg.norm(values[:, :3], axis=1)
            out.append(
                _nodal_field(
                    field.step,
                    node_ids,
                    "nodes",
                    "DISPLACEMENT",
                    ("ALL", "X", "Y", "Z", "RX", "RY", "RZ"),
                    np.column_stack((all_translation, values)),
                    derived=True,
                    coordinate_system="model",
                    field_type=NodalFieldType.DISP,
                )
            )
        elif field.name == "REACTION-FORCE":
            field.presentation = presentation(
                "nodes",
                "REACTION-FORCE",
                derived=False,
                coordinate_system="model",
            )
    return out


def _element_maps(mesh):
    nodes_by_element: dict[int, np.ndarray] = {}
    normal_by_element: dict[int, np.ndarray] = {}
    source_type_by_element: dict[int, int] = {}
    node_coord = {int(n): np.asarray(p, dtype=float) for n, p in zip(mesh.nodes.identifiers, mesh.nodes.coords)}
    for block in mesh.elements:
        for label, refs in zip(block.identifiers, block.node_refs):
            label_i = int(label)
            refs_arr = np.asarray(refs, dtype=int)
            nodes_by_element[label_i] = refs_arr
            source_type_by_element[label_i] = int(block.elem_info.source_type)
            if len(refs_arr) >= 3:
                p0, p1, p2 = (node_coord[int(n)] for n in refs_arr[:3])
                normal = np.cross(p1 - p0, p2 - p0)
                norm = float(np.linalg.norm(normal))
                normal_by_element[label_i] = normal / norm if norm else np.full(3, np.nan)
    return nodes_by_element, normal_by_element, source_type_by_element


def _geometry_by_element(mesh) -> dict[int, int]:
    if mesh.elem_data is None:
        return {}
    return {int(row[0]): int(row[2]) for row in np.asarray(mesh.elem_data)}


def _shell_surfaces(raw: ElementFieldData):
    values = np.asarray(raw.values, dtype=float)
    if values.ndim != 2 or values.shape[1] < 5:
        raise ValueError(f"raw shell field has unexpected shape {values.shape}")
    labels, counts = np.unique(values[:, 0].astype(int), return_counts=True)
    if not len(labels) or len(set(counts.tolist())) != 1:
        raise ValueError("raw shell field has ragged result-point counts")
    n_ips = int(counts[0])
    corner_indices = _SHELL_CORNER_INDICES.get(n_ips)
    if corner_indices is None or n_ips % 2:
        return None
    per_element = values.reshape(len(labels), n_ips, -1)
    # Preserve reader order and guard against np.unique sorting a different one.
    labels = per_element[:, 0, 0].astype(int)
    basic = per_element[:, :, 2:5]
    n_surface = n_ips // 2
    bottom = basic[:, :n_surface, :]
    top = basic[:, n_surface:, :]
    if bottom.shape != top.shape:
        raise ValueError("upper/lower shell result-point layouts differ")
    return labels, bottom, top, np.asarray(corner_indices, dtype=int)


def _shell_position_arrays(bottom, top, corner_indices):
    d_result = decompose_shell(bottom, top)
    arrays = {
        "resultpoints": (bottom, top, d_result),
        "elements": (
            bottom[:, corner_indices, :],
            top[:, corner_indices, :],
            d_result[:, corner_indices, :],
        ),
    }
    bottom_avg = bottom[:, corner_indices, :].mean(axis=1, keepdims=True)
    top_avg = top[:, corner_indices, :].mean(axis=1, keepdims=True)
    arrays["element_average"] = (bottom_avg, top_avg, decompose_shell(bottom_avg, top_avg))
    return arrays


def _wants(wanted: set[str] | None, position: str, attribute: str) -> bool:
    return wanted is None or semantic_name(position, attribute) in wanted


def _shell_fields_for_raw(raw, mesh, sif, nodal_contrib, wanted):
    surfaces = _shell_surfaces(raw)
    if surfaces is None:
        return []
    labels, bottom, top, corner_indices = surfaces
    nodes_by_element, normals, _ = _element_maps(mesh)
    geometry = _geometry_by_element(mesh)
    thickness_map = sif.get_shell_thickness_map()
    thickness = np.asarray([thickness_map.get(geometry.get(int(label), -1), np.nan) for label in labels])
    arrays = _shell_position_arrays(bottom, top, corner_indices)
    out: list[ElementFieldData] = []

    for position, (position_bottom, position_top, d_stress) in arrays.items():
        attributes = ("G-STRESS", "P-STRESS", "D-STRESS")
        if position != "resultpoints":
            attributes += ("PM-STRESS", "R-STRESS")
        if not any(_wants(wanted, position, attribute) for attribute in attributes):
            continue
        n_slots = position_top.shape[1]
        int_positions = [(i, str(i), 0.5) for i in range(n_slots)]
        if _wants(wanted, position, "G-STRESS"):
            out.append(
                _element_field(
                    raw,
                    position,
                    "G-STRESS",
                    G_STRESS_COMPONENTS,
                    labels,
                    general_stress(position_top),
                    derived=True,
                    int_positions=int_positions,
                )
            )
        if _wants(wanted, position, "P-STRESS"):
            out.append(
                _element_field(
                    raw,
                    position,
                    "P-STRESS",
                    P_STRESS_COMPONENTS,
                    labels,
                    plane_principal(position_top[..., 0], position_top[..., 1], position_top[..., 2]),
                    derived=True,
                    int_positions=int_positions,
                )
            )
        if _wants(wanted, position, "D-STRESS"):
            out.append(
                _element_field(
                    raw,
                    position,
                    "D-STRESS",
                    D_STRESS_COMPONENTS,
                    labels,
                    d_stress,
                    derived=True,
                    int_positions=int_positions,
                )
            )
        if position != "resultpoints" and _wants(wanted, position, "PM-STRESS"):
            out.append(
                _element_field(
                    raw,
                    position,
                    "PM-STRESS",
                    P_STRESS_COMPONENTS,
                    labels,
                    membrane_principal(d_stress),
                    derived=True,
                    int_positions=int_positions,
                )
            )
        if position != "resultpoints" and _wants(wanted, position, "R-STRESS"):
            out.append(
                _element_field(
                    raw,
                    position,
                    "R-STRESS",
                    R_STRESS_COMPONENTS,
                    labels,
                    stress_resultants(d_stress, thickness[:, None]),
                    derived=True,
                    int_positions=int_positions,
                )
            )

    # Keep paired basic stresses for the Xtract Nodes calculation. Values map
    # to connectivity order because corner_indices is Xtract's element-slot
    # order, not the raw RDPOINTS order.
    if any(
        _wants(wanted, "nodes", attribute)
        for attribute in ("G-STRESS", "P-STRESS", "PM-STRESS", "D-STRESS", "R-STRESS")
    ):
        corner_bottom = bottom[:, corner_indices, :]
        corner_top = top[:, corner_indices, :]
        for ei, label in enumerate(labels):
            refs = nodes_by_element.get(int(label))
            if refs is None or len(refs) != len(corner_indices):
                continue
            normal = normals.get(int(label), np.full(3, np.nan))
            for ci, node_id in enumerate(refs):
                nodal_contrib[int(node_id)].append(
                    (corner_bottom[ei, ci], corner_top[ei, ci], float(thickness[ei]), normal)
                )
    return out


def _average_nodal_shell(contrib, node_ids):
    bottom = np.full((len(node_ids), 3), np.nan)
    top = np.full((len(node_ids), 3), np.nan)
    thickness = np.full(len(node_ids), np.nan)
    cos_limit = np.cos(np.deg2rad(5.0))
    for ni, node_id in enumerate(node_ids):
        rows = contrib.get(int(node_id), ())
        # Xtract only creates a nodal average where at least two eligible
        # adjoining shell elements contribute. A lone boundary value remains
        # blank in the listing.
        if len(rows) < 2:
            continue
        ref_t = rows[0][2]
        ref_n = rows[0][3]
        eligible = []
        for row in rows:
            t = row[2]
            normal = row[3]
            thickness_ok = np.isfinite(t) and np.isfinite(ref_t) and abs(t - ref_t) <= 0.1 * max(abs(ref_t), 1e-30)
            normal_ok = np.all(np.isfinite(normal)) and np.all(np.isfinite(ref_n)) and float(np.dot(normal, ref_n)) >= cos_limit
            if thickness_ok and normal_ok:
                eligible.append(row)
        # Multiple non-coplanar/thickness groups at one node are ambiguous in a
        # single nodal scalar field. Match Xtract's blank rather than choosing a
        # group silently.
        if len(eligible) != len(rows) or len(eligible) < 2:
            continue
        bottom[ni] = np.mean([row[0] for row in eligible], axis=0)
        top[ni] = np.mean([row[1] for row in eligible], axis=0)
        thickness[ni] = float(np.mean([row[2] for row in eligible]))
    return bottom, top, thickness


def _nodal_shell_fields(step, node_ids, contrib, wanted):
    bottom, top, thickness = _average_nodal_shell(contrib, node_ids)
    d = decompose_shell(bottom, top)
    out = []
    if _wants(wanted, "nodes", "G-STRESS"):
        out.append(_nodal_field(
            step,
            node_ids,
            "nodes",
            "G-STRESS",
            G_STRESS_COMPONENTS,
            general_stress(top),
            derived=True,
            coordinate_system="element_local",
        ))
    if _wants(wanted, "nodes", "P-STRESS"):
        out.append(_nodal_field(
            step,
            node_ids,
            "nodes",
            "P-STRESS",
            P_STRESS_COMPONENTS,
            plane_principal(top[..., 0], top[..., 1], top[..., 2]),
            derived=True,
            coordinate_system="element_local",
        ))
    if _wants(wanted, "nodes", "PM-STRESS"):
        out.append(_nodal_field(
            step,
            node_ids,
            "nodes",
            "PM-STRESS",
            P_STRESS_COMPONENTS,
            membrane_principal(d),
            derived=True,
            coordinate_system="element_local",
        ))
    if _wants(wanted, "nodes", "D-STRESS"):
        out.append(_nodal_field(
            step,
            node_ids,
            "nodes",
            "D-STRESS",
            D_STRESS_COMPONENTS,
            d,
            derived=True,
            coordinate_system="element_local",
        ))
    if _wants(wanted, "nodes", "R-STRESS"):
        out.append(_nodal_field(
            step,
            node_ids,
            "nodes",
            "R-STRESS",
            R_STRESS_COMPONENTS,
            stress_resultants(d, thickness),
            derived=True,
            coordinate_system="element_local",
        ))
    return out


def _profile_extents(sif) -> dict[int, tuple[float, float]]:
    extents: dict[int, tuple[float, float]] = {}
    for card, height_name, width_names in (
        (cards.GIORH, "hz", ("bt", "bb")),
        (cards.GBOX, "hz", ("by",)),
        (cards.GLSEC, "hz", ("by",)),
        (cards.GPIPE, "dy", ("dy",)),
    ):
        rows = sif._sections.get(card.name, []) or []
        geono_i = card.get_indices_from_names(["geono"])
        height_i = card.get_indices_from_names([height_name])
        width_i = [card.get_indices_from_names([name]) for name in width_names]
        for row in rows:
            extents[int(row[geono_i])] = (
                float(row[height_i]),
                max(float(row[i]) for i in width_i),
            )
    return extents


def _beam_properties(sif, mesh, labels):
    geometry = _geometry_by_element(mesh)
    props = sif.get_gbeamg_map()
    extents = _profile_extents(sif)
    names = ("area", "ix", "iy", "iz", "wxmin", "wymin", "wzmin", "shary", "sharz")
    indices = cards.GBEAMG.get_indices_from_names(list(names))
    out = []
    for label in labels:
        geono = geometry.get(int(label), -1)
        row = props.get(geono)
        if row is None:
            out.append(None)
            continue
        values = {name: float(row[i]) for name, i in zip(names, indices)}
        height, width = extents.get(geono, (np.nan, np.nan))
        values["wymin2"] = opposite_section_modulus(values["iy"], values["wymin"], height)
        values["wzmin2"] = opposite_section_modulus(values["iz"], values["wzmin"], width)
        out.append(values)
    return out


def _beam_fields_for_raw(raw, mesh, sif, wanted):
    values = np.asarray(raw.values, dtype=float)
    labels, counts = np.unique(values[:, 0].astype(int), return_counts=True)
    if not len(labels) or len(set(counts.tolist())) != 1:
        return []
    n_ips = int(counts[0])
    per_element = values.reshape(len(labels), n_ips, -1)
    labels = per_element[:, 0, 0].astype(int)
    force = per_element[:, :, 2:8]
    properties = _beam_properties(sif, mesh, labels)
    b_stress = np.full(force.shape[:-1] + (8,), np.nan)
    for i, prop in enumerate(properties):
        if prop is not None:
            b_stress[i] = beam_stress(force[i], **{k: prop[k] for k in (
                "area", "wxmin", "wymin", "wzmin", "shary", "sharz", "wymin2", "wzmin2"
            )})

    position_indices = {
        "resultpoints": np.arange(n_ips),
        "elements": np.asarray((0, n_ips - 1)),
    }
    out = []
    for position, indices in position_indices.items():
        if _wants(wanted, position, "G-FORCE"):
            out.append(
            _element_field(
                raw,
                position,
                "G-FORCE",
                G_FORCE_COMPONENTS,
                labels,
                force[:, indices, :],
                derived=False,
                line=True,
            )
        )
        if _wants(wanted, position, "B-STRESS"):
            out.append(
            _element_field(
                raw,
                position,
                "B-STRESS",
                B_STRESS_COMPONENTS,
                labels,
                b_stress[:, indices, :],
                derived=True,
                line=True,
            )
        )
    force_avg = force[:, (0, n_ips - 1), :].mean(axis=1, keepdims=True)
    b_avg = np.full(force_avg.shape[:-1] + (8,), np.nan)
    for i, prop in enumerate(properties):
        if prop is not None:
            b_avg[i] = beam_stress(force_avg[i], **{k: prop[k] for k in (
                "area", "wxmin", "wymin", "wzmin", "shary", "sharz", "wymin2", "wzmin2"
            )})
    if _wants(wanted, "element_average", "G-FORCE"):
        out.append(
        _element_field(
            raw,
            "element_average",
            "G-FORCE",
            G_FORCE_COMPONENTS,
            labels,
            force_avg,
            derived=False,
            line=True,
        )
    )
    if _wants(wanted, "element_average", "B-STRESS"):
        out.append(
        _element_field(
            raw,
            "element_average",
            "B-STRESS",
            B_STRESS_COMPONENTS,
            labels,
            b_avg,
            derived=True,
            line=True,
        )
    )
    return out


def build_xtract_fields(
    raw_fields: list[ElementFieldData],
    mesh,
    sif,
    *,
    wanted: set[str] | None = None,
) -> list[ElementFieldData | NodalFieldData]:
    """Derive every currently-supported Xtract field from one loaded step."""

    out: list[ElementFieldData | NodalFieldData] = []
    shell_by_step: dict[int, list[ElementFieldData]] = defaultdict(list)
    force_by_step: dict[int, list[ElementFieldData]] = defaultdict(list)
    for raw in raw_fields:
        if raw.name == "STRESS":
            shell_by_step[int(raw.step)].append(raw)
        elif raw.name == "FORCES":
            force_by_step[int(raw.step)].append(raw)

    node_ids = np.asarray(mesh.nodes.identifiers, dtype=int)
    for step, shell_fields in shell_by_step.items():
        shell_attributes = ("G-STRESS", "P-STRESS", "PM-STRESS", "D-STRESS", "R-STRESS")
        if wanted is not None and not any(
            semantic_name(position, attribute) in wanted
            for position in ("nodes", "elements", "element_average", "resultpoints")
            for attribute in shell_attributes
        ):
            continue
        contrib = defaultdict(list)
        for raw in shell_fields:
            out.extend(_shell_fields_for_raw(raw, mesh, sif, contrib, wanted))
        if any(_wants(wanted, "nodes", attribute) for attribute in shell_attributes):
            out.extend(_nodal_shell_fields(step, node_ids, contrib, wanted))
    for force_fields in force_by_step.values():
        if wanted is not None and not any(
            semantic_name(position, attribute) in wanted
            for position in ("elements", "element_average", "resultpoints")
            for attribute in ("G-FORCE", "B-STRESS")
        ):
            continue
        for raw in force_fields:
            out.extend(_beam_fields_for_raw(raw, mesh, sif, wanted))
    return out


__all__ = ["build_xtract_fields", "build_xtract_nodal_kinematics"]

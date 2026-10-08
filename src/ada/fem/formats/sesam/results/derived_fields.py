"""Build derived fields from raw Sesam SIN/SIF result records.

This module owns the source-specific position semantics. The frontend receives
explicit supports and never has to guess whether averaging a Gauss-point field
is equivalent to the reference postprocessor (it generally is not).

The first supported shell families are Sesam type 24/25 (four-node quad and
three-node triangle), which are the shell families in the Sesam validation
model. Unsupported layouts retain the raw STRESS field but do not advertise
derived fields.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.derived_values import (
    B_STRESS_COMPONENTS,
    BEAM_STRESS_DENOMINATORS,
    D_STRESS_COMPONENTS,
    G_FORCE_COMPONENTS,
    G_STRESS_COMPONENTS,
    P_STRESS_COMPONENTS,
    R_STRESS_COMPONENTS,
    beam_stress_per_element,
    decompose_shell,
    general_stress,
    membrane_principal,
    opposite_section_modulus,
    plane_principal,
    stress_resultants,
)
from ada.fem.formats.sesam.results.result_catalog import presentation, semantic_name
from ada.fem.formats.sesam.results.result_units import (
    common_result_unit,
    result_component_units,
)
from ada.fem.results.field_data import (
    ElementFieldData,
    FieldPosition,
    NodalFieldData,
    NodalFieldType,
)

_SHELL_CORNER_INDICES = {
    # Surface/result-point order is node0, node1, centre, node3, node2
    # for FQUS. The reference postprocessor's Elements slots follow connectivity order.
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
    surface: str = "",
    unit_factors: tuple[float, float, float] | None = None,
) -> ElementFieldData:
    values = np.asarray(values, dtype=float)
    if values.ndim != 3 or values.shape[0] != len(labels):
        raise ValueError(f"{position}/{attribute}: expected (elements, slots, components), got {values.shape}")
    n_slots = values.shape[1]
    rows = np.empty((len(labels) * n_slots, 2 + values.shape[2]), dtype=float)
    rows[:, 0] = np.repeat(labels, n_slots)
    rows[:, 1] = np.tile(np.arange(1, n_slots + 1), len(labels))
    rows[:, 2:] = values.reshape(-1, values.shape[2])
    component_units = result_component_units(unit_factors, attribute, components)
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
            surface=surface,
            unit=common_result_unit(component_units),
            component_units=component_units,
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
    surface: str = "",
    unit_factors: tuple[float, float, float] | None = None,
) -> NodalFieldData:
    canonical_name = semantic_name(position, attribute)
    name = f"{canonical_name}.{surface}" if surface == "lower" else canonical_name
    data = np.column_stack((node_ids, np.asarray(values, dtype=float)))
    component_units = result_component_units(unit_factors, attribute, components)
    return NodalFieldData(
        name=name,
        step=int(step),
        components=list(components),
        values=data,
        field_type=field_type,
        presentation=presentation(
            position,
            attribute,
            derived=derived,
            coordinate_system=coordinate_system,
            surface=surface,
            unit=common_result_unit(component_units),
            component_units=component_units,
        ),
    )


def build_nodal_kinematics(
    nodal_fields: list[NodalFieldData],
    node_ids: np.ndarray,
    *,
    wanted: set[str] | None = None,
    unit_factors: tuple[float, float, float] | None = None,
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
                    unit_factors=unit_factors,
                )
            )
        elif field.name == "REACTION-FORCE":
            field.presentation = presentation(
                "nodes",
                "REACTION-FORCE",
                derived=False,
                coordinate_system="model",
                unit=common_result_unit(result_component_units(unit_factors, "REACTION-FORCE", field.components)),
                component_units=result_component_units(unit_factors, "REACTION-FORCE", field.components),
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
    # SIN result words are IEEE float32. Preserve that precision through the
    # position averaging which precedes derived calculations in the reference postprocessor;
    # promoting before the average changes cancellation-sensitive values.
    basic = np.asarray(per_element[:, :, 2:5], dtype=np.float32)
    n_surface = n_ips // 2
    bottom = basic[:, :n_surface, :]
    top = basic[:, n_surface:, :]
    if bottom.shape != top.shape:
        raise ValueError("upper/lower shell result-point layouts differ")
    raw_positions = raw.int_positions or []
    in_plane = []
    for entry in raw_positions[:n_surface]:
        location = entry[1]
        # Some legacy INT_LOCATIONS tuples repeat the thickness coordinate
        # inside the in-plane value. Surface is carried separately below.
        if isinstance(location, tuple) and len(location) == 3:
            location = location[:2]
        in_plane.append(location)
    if len(in_plane) != n_surface:
        in_plane = list(range(n_surface))
    return labels, bottom, top, np.asarray(corner_indices, dtype=int), in_plane


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


def _surface_values_and_positions(bottom: np.ndarray, top: np.ndarray, in_plane=None):
    """Pack selectable shell surfaces into one AFEL integration-point axis.

    Top comes first to retain the reference postprocessor's upper-surface default and listing slot
    order. The signed third entry is converted to ``top``/``bottom`` by the
    generic artefact writer, so no Sesam-specific surface logic is needed in
    the viewer.
    """

    if bottom.shape != top.shape:
        raise ValueError("upper/lower shell field layouts differ")
    n_slots = top.shape[1]
    locations = list(in_plane) if in_plane is not None else [str(i + 1) for i in range(n_slots)]
    if len(locations) != n_slots:
        raise ValueError("shell result locations do not match the surface slot count")
    values = np.concatenate((top, bottom), axis=1)
    int_positions = [
        *((i, locations[i], 0.5) for i in range(n_slots)),
        *((n_slots + i, locations[i], -0.5) for i in range(n_slots)),
    ]
    return values, int_positions


def _wants(wanted: set[str] | None, position: str, attribute: str) -> bool:
    name = semantic_name(position, attribute)
    return wanted is None or name in wanted or f"{name}.lower" in wanted


def _wants_nodal_surface(wanted: set[str] | None, attribute: str, surface: str) -> bool:
    name = semantic_name("nodes", attribute)
    requested = f"{name}.lower" if surface == "lower" else name
    return wanted is None or requested in wanted


def _once(cache: dict | None, key: str, make):
    """``make()``, or what it returned the first time for ``key`` in ``cache``.

    The mesh- and model-wide lookups below do not change from one step to the
    next; built per raw field, a deck with a hundred result cases rebuilt them a
    hundred times (a Python pass over every element each)."""
    if cache is None:
        return make()
    if key not in cache:
        cache[key] = make()
    return cache[key]


def _lookup_cache(mesh, sif) -> dict:
    """The :func:`_once` cache for ``mesh`` and the reader ``sif`` it was read with.

    Kept on the reader: a streaming read (one result case at a time) keeps one
    reader and one mesh and derives every case over them, so this is what spares
    each case rebuilding the lookups -- and it goes when the reader goes, rather
    than riding along on the mesh of the result. A different mesh starts a fresh
    cache; a reader that takes no attributes gets one per call."""
    cache = getattr(sif, "_derived_lookups", None)
    if cache is None or cache.get("mesh") is not mesh:
        cache = {"mesh": mesh}
        try:
            sif._derived_lookups = cache
        except AttributeError:
            pass
    return cache


def _shell_fields_for_raw(raw, mesh, sif, nodal_contrib, wanted, cache: dict | None = None):
    surfaces = _shell_surfaces(raw)
    if surfaces is None:
        return []
    labels, bottom, top, corner_indices, result_locations = surfaces
    nodes_by_element, normals, _ = _once(cache, "element_maps", lambda: _element_maps(mesh))
    geometry = _once(cache, "geometry", lambda: _geometry_by_element(mesh))
    thickness_map = _once(cache, "thickness", sif.get_shell_thickness_map)
    unit_factors = sif.get_unit_factors()
    thickness = np.asarray([thickness_map.get(geometry.get(int(label), -1), np.nan) for label in labels])
    arrays = _shell_position_arrays(bottom, top, corner_indices)
    out: list[ElementFieldData] = []

    for position, (position_bottom, position_top, d_stress) in arrays.items():
        attributes = ("G-STRESS", "P-STRESS", "D-STRESS")
        if position != "resultpoints":
            attributes += ("PM-STRESS", "R-STRESS")
        if not any(_wants(wanted, position, attribute) for attribute in attributes):
            continue
        if position == "resultpoints":
            in_plane = result_locations
        elif position == "elements":
            in_plane = [result_locations[i] for i in corner_indices]
        else:
            in_plane = ["centroid"]
        surface_basic, surface_positions = _surface_values_and_positions(position_bottom, position_top, in_plane)
        n_slots = position_top.shape[1]
        paired_positions = [(i, in_plane[i], 0.0) for i in range(n_slots)]
        if _wants(wanted, position, "G-STRESS"):
            out.append(
                _element_field(
                    raw,
                    position,
                    "G-STRESS",
                    G_STRESS_COMPONENTS,
                    labels,
                    general_stress(surface_basic),
                    derived=True,
                    int_positions=surface_positions,
                    surface="selectable",
                    unit_factors=unit_factors,
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
                    plane_principal(surface_basic[..., 0], surface_basic[..., 1], surface_basic[..., 2]),
                    derived=True,
                    int_positions=surface_positions,
                    surface="selectable",
                    unit_factors=unit_factors,
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
                    int_positions=paired_positions,
                    unit_factors=unit_factors,
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
                    int_positions=paired_positions,
                    unit_factors=unit_factors,
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
                    int_positions=paired_positions,
                    unit_factors=unit_factors,
                )
            )

    # Keep paired basic stresses for the Nodes calculation. Values map
    # to connectivity order because corner_indices is the reference postprocessor's element-slot
    # order, not the raw RDPOINTS order.
    if any(
        _wants(wanted, "nodes", attribute)
        for attribute in ("G-STRESS", "P-STRESS", "PM-STRESS", "D-STRESS", "R-STRESS")
    ):
        element_rows, refs, element_normals = _once(
            cache,
            ("shell_corners", labels.tobytes(), len(corner_indices)),
            lambda: _shell_corner_nodes(labels, nodes_by_element, normals, len(corner_indices)),
        )
        nodal_contrib.add(
            refs,
            bottom[element_rows][:, corner_indices, :],
            top[element_rows][:, corner_indices, :],
            thickness[element_rows],
            element_normals,
        )
    return out


def _shell_corner_nodes(labels, nodes_by_element, normals, n_corners: int):
    """The elements of ``labels`` whose corners feed the nodal average, as arrays.

    ``(rows, refs, normals)``: the row of each such element in ``labels``, its
    ``n_corners`` corner node ids in connectivity order, and its unit normal (NaN
    when unknown). An element without connectivity, or with another corner count,
    contributes nothing."""
    rows, refs, normal_rows = [], [], []
    missing = np.full(3, np.nan)
    for ei, label in enumerate(labels):
        element_refs = nodes_by_element.get(int(label))
        if element_refs is None or len(element_refs) != n_corners:
            continue
        rows.append(ei)
        refs.append(element_refs)
        normal_rows.append(normals.get(int(label), missing))
    return (
        np.asarray(rows, dtype=int),
        np.asarray(refs, dtype=int).reshape(len(rows), n_corners),
        np.asarray(normal_rows, dtype=float).reshape(len(rows), 3),
    )


class _ShellNodalContributions:
    """Every shell element corner's basic stresses, gathered for the nodal average.

    One step's contributions arrive raw field by raw field as arrays
    (:meth:`add`); :meth:`average` reduces them per node. Equivalent to keeping a
    list of ``(bottom, top, thickness, normal)`` per node in arrival order, without
    a Python object per corner per step.
    """

    def __init__(self) -> None:
        self._chunks: list[tuple] = []

    def add(self, refs, bottom, top, thickness, normals) -> None:
        """``refs`` ``(n, c)`` corner node ids; ``bottom`` / ``top`` ``(n, c, 3)``;
        ``thickness`` ``(n,)``; ``normals`` ``(n, 3)`` -- per element, corners in order."""
        n, c = refs.shape
        if not n:
            return
        self._chunks.append(
            (
                refs.reshape(-1),
                np.asarray(bottom, dtype=np.float32).reshape(n * c, 3),
                np.asarray(top, dtype=np.float32).reshape(n * c, 3),
                np.repeat(np.asarray(thickness, dtype=float), c),
                np.repeat(np.asarray(normals, dtype=float), c, axis=0),
            )
        )

    def average(self, node_ids):
        """``(bottom, top, thickness)`` averaged per node of ``node_ids``; NaN where not.

        The reference postprocessor only creates a nodal average where at least
        two adjoining shell elements contribute AND all of them agree with the
        first on thickness (within 10 %) and normal (within 5 degrees). A lone
        boundary value, or several non-coplanar / thickness groups meeting at one
        node -- ambiguous in a single nodal scalar field -- stay blank rather than
        one group being chosen silently. The means add up in arrival order, in
        float32 for the stresses, as ``np.mean`` over each node's list does.
        """
        node_ids = np.asarray(node_ids)
        bottom = np.full((len(node_ids), 3), np.nan, dtype=np.float32)
        top = np.full((len(node_ids), 3), np.nan, dtype=np.float32)
        thickness = np.full(len(node_ids), np.nan)
        if not self._chunks or not len(node_ids):
            return bottom, top, thickness
        nodes, b, t, tk, nr = (np.concatenate(parts) for parts in zip(*self._chunks))
        order = np.argsort(nodes, kind="stable")
        nodes, b, t, tk, nr = nodes[order], b[order], t[order], tk[order], nr[order]
        groups, starts, counts = np.unique(nodes, return_index=True, return_counts=True)

        first = np.repeat(starts, counts)
        ref_t, ref_n = tk[first], nr[first]
        cos_limit = np.cos(np.deg2rad(5.0))
        with np.errstate(invalid="ignore"):
            thickness_ok = (
                np.isfinite(tk) & np.isfinite(ref_t) & (np.abs(tk - ref_t) <= 0.1 * np.maximum(np.abs(ref_t), 1e-30))
            )
            normal_ok = (
                np.all(np.isfinite(nr), axis=1)
                & np.all(np.isfinite(ref_n), axis=1)
                & (np.einsum("ij,ij->i", nr, ref_n) >= cos_limit)
            )
        averaged = np.logical_and.reduceat(thickness_ok & normal_ok, starts) & (counts >= 2)

        # np.mean's order of addition: one value after another onto the first, in
        # float32 for the stresses -- for fewer than eight values; beyond, it is
        # simply asked (below). Same order, same roundings. (np.add.reduceat
        # associates differently, and the last bit shows.)
        sum_b, sum_t, sum_tk = b[starts].copy(), t[starts].copy(), tk[starts].copy()
        for k in range(1, min(int(counts.max()), 8)):
            more = np.flatnonzero(counts > k)
            sum_b[more] += b[starts[more] + k]
            sum_t[more] += t[starts[more] + k]
            sum_tk[more] += tk[starts[more] + k]
        mean_b = (sum_b.astype(float) / counts[:, None]).astype(np.float32)
        mean_t = (sum_t.astype(float) / counts[:, None]).astype(np.float32)
        mean_tk = sum_tk / counts
        for g in np.flatnonzero(averaged & (counts >= 8)):
            span = slice(starts[g], starts[g] + counts[g])
            mean_b[g], mean_t[g] = np.mean(b[span], axis=0), np.mean(t[span], axis=0)
            mean_tk[g] = float(np.mean(tk[span]))

        at = np.searchsorted(groups, node_ids)
        found = at < len(groups)
        found[found] = groups[at[found]] == node_ids[found]
        hit = np.flatnonzero(found)
        hit = hit[averaged[at[hit]]]
        bottom[hit] = mean_b[at[hit]]
        top[hit] = mean_t[at[hit]]
        thickness[hit] = mean_tk[at[hit]]
        return bottom, top, thickness


def _nodal_shell_fields(step, node_ids, contrib, wanted, unit_factors):
    bottom, top, thickness = contrib.average(node_ids)
    d = decompose_shell(bottom, top)
    out = []
    for surface, basic in (("upper", top), ("lower", bottom)):
        if _wants_nodal_surface(wanted, "G-STRESS", surface):
            out.append(
                _nodal_field(
                    step,
                    node_ids,
                    "nodes",
                    "G-STRESS",
                    G_STRESS_COMPONENTS,
                    general_stress(basic),
                    derived=True,
                    coordinate_system="element_local",
                    surface=surface,
                    unit_factors=unit_factors,
                )
            )
        if _wants_nodal_surface(wanted, "P-STRESS", surface):
            out.append(
                _nodal_field(
                    step,
                    node_ids,
                    "nodes",
                    "P-STRESS",
                    P_STRESS_COMPONENTS,
                    plane_principal(basic[..., 0], basic[..., 1], basic[..., 2]),
                    derived=True,
                    coordinate_system="element_local",
                    surface=surface,
                    unit_factors=unit_factors,
                )
            )
    if _wants(wanted, "nodes", "PM-STRESS"):
        out.append(
            _nodal_field(
                step,
                node_ids,
                "nodes",
                "PM-STRESS",
                P_STRESS_COMPONENTS,
                membrane_principal(d),
                derived=True,
                coordinate_system="element_local",
                unit_factors=unit_factors,
            )
        )
    if _wants(wanted, "nodes", "D-STRESS"):
        out.append(
            _nodal_field(
                step,
                node_ids,
                "nodes",
                "D-STRESS",
                D_STRESS_COMPONENTS,
                d,
                derived=True,
                coordinate_system="element_local",
                unit_factors=unit_factors,
            )
        )
    if _wants(wanted, "nodes", "R-STRESS"):
        out.append(
            _nodal_field(
                step,
                node_ids,
                "nodes",
                "R-STRESS",
                R_STRESS_COMPONENTS,
                stress_resultants(d, thickness),
                derived=True,
                coordinate_system="element_local",
                unit_factors=unit_factors,
            )
        )
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


def _beam_properties(sif, mesh, labels, cache: dict | None = None):
    geometry = _once(cache, "geometry", lambda: _geometry_by_element(mesh))
    props = _once(cache, "gbeamg", sif.get_gbeamg_map)
    extents = _once(cache, "profile_extents", lambda: _profile_extents(sif))
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


def _beam_denominators(properties) -> np.ndarray:
    """``(n, 8)`` :data:`BEAM_STRESS_DENOMINATORS` per element; NaN where it has no section.

    A missing opposite-side modulus falls back to the primary one, as
    :func:`beam_stress` does for ``wymin2=None`` / ``wzmin2=None``."""
    out = np.full((len(properties), len(BEAM_STRESS_DENOMINATORS)), np.nan)
    fallback = {"wymin2": "wymin", "wzmin2": "wzmin"}
    for i, prop in enumerate(properties):
        if prop is None:
            continue
        out[i] = [
            prop[fallback[name]] if name in fallback and prop.get(name) is None else prop[name]
            for name in BEAM_STRESS_DENOMINATORS
        ]
    return out


def _beam_fields_for_raw(raw, mesh, sif, wanted, cache: dict | None = None):
    values = np.asarray(raw.values, dtype=float)
    labels, counts = np.unique(values[:, 0].astype(int), return_counts=True)
    if not len(labels) or len(set(counts.tolist())) != 1:
        return []
    n_ips = int(counts[0])
    per_element = values.reshape(len(labels), n_ips, -1)
    labels = per_element[:, 0, 0].astype(int)
    force = per_element[:, :, 2:8]
    denominators = _once(
        cache,
        ("beam_denominators", labels.tobytes()),
        lambda: _beam_denominators(_beam_properties(sif, mesh, labels, cache)),
    )
    unit_factors = sif.get_unit_factors()
    b_stress = beam_stress_per_element(force, denominators)

    position_indices = {
        "resultpoints": np.arange(n_ips),
        "elements": np.asarray((0, n_ips - 1)),
    }
    out = []
    for position, indices in position_indices.items():
        raw_positions = raw.int_positions or [(i, i / max(n_ips - 1, 1)) for i in range(n_ips)]
        int_positions = [(i, raw_positions[int(source)][1]) for i, source in enumerate(indices)]
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
                    int_positions=int_positions,
                    unit_factors=unit_factors,
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
                    int_positions=int_positions,
                    unit_factors=unit_factors,
                )
            )
    force_avg = force[:, (0, n_ips - 1), :].mean(axis=1, keepdims=True)
    b_avg = beam_stress_per_element(force_avg, denominators)
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
                int_positions=[(0, 0.5)],
                unit_factors=unit_factors,
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
                int_positions=[(0, 0.5)],
                unit_factors=unit_factors,
            )
        )
    return out


def build_derived_fields(
    raw_fields: list[ElementFieldData],
    mesh,
    sif,
    *,
    wanted: set[str] | None = None,
) -> list[ElementFieldData | NodalFieldData]:
    """Derive every currently-supported derived field from one loaded step."""

    out: list[ElementFieldData | NodalFieldData] = []
    shell_by_step: dict[int, list[ElementFieldData]] = defaultdict(list)
    force_by_step: dict[int, list[ElementFieldData]] = defaultdict(list)
    for raw in raw_fields:
        if raw.name == "STRESS":
            shell_by_step[int(raw.step)].append(raw)
        elif raw.name == "FORCES":
            force_by_step[int(raw.step)].append(raw)

    node_ids = np.asarray(mesh.nodes.identifiers, dtype=int)
    # Mesh- and model-wide lookups, built once for every step (see _once) --
    # and, kept on the reader, once for a streaming read that derives one step
    # at a time over the same mesh.
    cache = _lookup_cache(mesh, sif)
    for step, shell_fields in shell_by_step.items():
        shell_attributes = ("G-STRESS", "P-STRESS", "PM-STRESS", "D-STRESS", "R-STRESS")
        if wanted is not None and not any(
            _wants(wanted, position, attribute)
            for position in ("nodes", "elements", "element_average", "resultpoints")
            for attribute in shell_attributes
        ):
            continue
        contrib = _ShellNodalContributions()
        for raw in shell_fields:
            out.extend(_shell_fields_for_raw(raw, mesh, sif, contrib, wanted, cache))
        if any(_wants(wanted, "nodes", attribute) for attribute in shell_attributes):
            out.extend(_nodal_shell_fields(step, node_ids, contrib, wanted, sif.get_unit_factors()))
    for force_fields in force_by_step.values():
        if wanted is not None and not any(
            _wants(wanted, position, attribute)
            for position in ("elements", "element_average", "resultpoints")
            for attribute in ("G-FORCE", "B-STRESS")
        ):
            continue
        for raw in force_fields:
            out.extend(_beam_fields_for_raw(raw, mesh, sif, wanted, cache))
    return out


__all__ = ["build_derived_fields", "build_nodal_kinematics"]

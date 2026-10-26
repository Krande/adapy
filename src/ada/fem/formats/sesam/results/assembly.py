"""A Sesam superelement assembly as one result: every superelement placed and merged.

A SIN written for a superelement assembly holds one RESULTS entry per superelement, each
with its own mesh, numbering and result tables, plus the hierarchy that places them
(HSUPTRAN). :func:`~.read_sin.read_sin_file` reads each superelement on its own; this
merges them into one :class:`FEAResult` -- what Xtract shows when it opens the assembly.

* **Placement.** Each superelement's node coordinates go through its placement (the
  transforms of it and every parent above it, composed). Vectors stored in the
  superelement's system turn with it: nodal displacements, rotations and reactions, the
  beam orientation vectors and the eccentricities. Element-local quantities (stresses,
  beam forces) do not.
* **Numbering.** Superelements number their nodes and elements independently, so element
  1 exists in each of them. Every id gets a per-superelement prefix that keeps it
  readable: superelement type × 10^k + its own number (SEL12's element 512 is 12000512
  for k = 6; k grows to fit the largest id). Two instances of one type, or a prefix that
  would not fit 32 bits, fall back to the superelement's position in the hierarchy. The
  prefixes are recorded on the result as ``sesam_super_elements``.
* **Fields.** One field per name and step, its rows the superelements' rows together; a
  superelement without a field (no stresses requested there) simply contributes none.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import TYPE_CHECKING

import numpy as np

from ada.fem.formats.sesam.results.property_fields import MATERIAL_FIELD, SECTION_FIELD
from ada.fem.results.field_data import NodalFieldData

if TYPE_CHECKING:
    from ada.fem.formats.sesam.results.sin_reader import SuperElementInfo
    from ada.fem.results.common import FEAResult

__all__ = ["assemble_super_elements", "placements", "id_bases"]

_UINT32_MAX = 2**32 - 1
# Component triplets that are vectors in the superelement's system.
_VECTOR_TRIPLETS = (
    ("U1", "U2", "U3"),
    ("U4", "U5", "U6"),
    ("X", "Y", "Z"),
    ("RX", "RY", "RZ"),
    ("X-FORCE", "Y-FORCE", "Z-FORCE"),
    ("RX-MOMENT", "RY-MOMENT", "RZ-MOMENT"),
)


def placements(rows: tuple[SuperElementInfo, ...]) -> dict[int, np.ndarray]:
    """``{ihref: 4x4 placement in the top level's system}``, composed down the hierarchy."""
    by_ref = {r.ihref: r for r in rows}
    out: dict[int, np.ndarray] = {}

    def resolve(ref) -> np.ndarray:
        if ref in out:
            return out[ref]
        row = by_ref[ref]
        own = np.eye(4) if row.transform is None else np.reshape(np.asarray(row.transform, dtype=float), (4, 4))
        parent = resolve(row.parent) if row.parent is not None and row.parent in by_ref else np.eye(4)
        out[ref] = parent @ own
        return out[ref]

    for r in rows:
        resolve(r.ihref)
    return out


def _max_id(result: FEAResult) -> int:
    mesh = result.mesh
    values = [np.asarray(mesh.nodes.identifiers), np.asarray(mesh.nodes.shown_ids())]
    values += [np.asarray(b.identifiers) for b in mesh.elements] + [np.asarray(b.shown_ids()) for b in mesh.elements]
    for d in (mesh.sections, mesh.materials, mesh.vectors):
        if d:
            values.append(np.asarray([int(k) for k in d.keys()]))
    return int(max((int(v.max()) for v in values if v.size), default=0))


def id_bases(rows: list[SuperElementInfo], results: list[FEAResult]) -> list[int]:
    """The id prefix of each superelement: ``seltyp × 10^k``, or by position when that is
    ambiguous (two instances of one type) or would not fit 32 bits."""
    largest = max((_max_id(r) for r in results), default=0)
    k = max(6, len(str(largest)))
    scale = 10**k
    seltyps = [r.seltyp for r in rows]
    if len(set(seltyps)) == len(seltyps) and (max(seltyps) + 1) * scale - 1 <= _UINT32_MAX:
        return [s * scale for s in seltyps]
    if (len(rows) + 1) * scale - 1 > _UINT32_MAX:
        raise ValueError(f"cannot number {len(rows)} superelements with ids up to {largest} in 32 bits")
    return [(i + 1) * scale for i in range(len(rows))]


def _rotate_rows(values: np.ndarray, components: list[str], rotation: np.ndarray, first_col: int) -> np.ndarray:
    """``values`` with each vector triplet of ``components`` turned by ``rotation``."""
    names = list(components)
    out = values
    for triplet in _VECTOR_TRIPLETS:
        if all(c in names for c in triplet):
            cols = [first_col + names.index(c) for c in triplet]
            if out is values:
                out = values.copy()
            out[:, cols] = out[:, cols] @ rotation.T
    return out


def _offset_keys(d: dict | None, base: int) -> dict:
    return {int(k) + base: v for k, v in (d or {}).items()}


def _offset_ref(col: np.ndarray, base: int) -> np.ndarray:
    """``col`` + ``base`` where it references something (> 0); 0 / -1 stay "none"."""
    col = np.asarray(col, dtype=float)
    return np.where(col > 0, col + base, col)


def _merge_parts(parts: list):
    """One field from the superelements' parts of it: their rows together, and (for a
    categorical property) every part's legend labels."""
    first = parts[0]
    if len(parts) == 1:
        return first
    changes = {"values": np.vstack([p.values for p in parts])}
    if first.presentation is not None and any(p.presentation and p.presentation.value_labels for p in parts):
        labels = {}
        for p in parts:
            for value, text in p.presentation.value_labels if p.presentation else ():
                labels.setdefault(value, text)
        changes["presentation"] = dataclasses.replace(first.presentation, value_labels=tuple(sorted(labels.items())))
    return dataclasses.replace(first, **changes)


def _super_element_fields(rows, blocks_by_part, fields) -> list:
    """``props.super_element``: which superelement each element belongs to, per element type.

    A categorical property like material and section, so the viewer colours the model by
    superelement with a legend naming them. On the step the other property fields use."""
    from ada.fem.formats.sesam.results.property_fields import (
        PROPERTY_FIELD_NAMES,
        super_element_field,
    )

    prop_steps = [int(f.step) for f in fields if f.name in PROPERTY_FIELD_NAMES]
    steps = prop_steps or [int(f.step) for f in fields]
    if not steps:
        return []
    step = min(steps)
    value_labels = [(float(k + 1), row.label) for k, row in enumerate(rows)]

    by_type: dict = {}
    for k, part_blocks in enumerate(blocks_by_part):
        for block in part_blocks:
            labels = np.asarray(block.identifiers, dtype=np.int64)
            if labels.size:
                by_type.setdefault(block.elem_info.type, []).append((labels, np.full(labels.size, k + 1.0)))
    return [
        super_element_field(
            step,
            elem_type,
            np.concatenate([lab for lab, _ in parts]),
            np.concatenate([code for _, code in parts]),
            value_labels,
        )
        for elem_type, parts in by_type.items()
    ]


def assemble_super_elements(
    rows: list[SuperElementInfo],
    results: list[FEAResult],
    placement: dict[int, np.ndarray],
    name: str,
    results_file_path: pathlib.Path | None = None,
) -> FEAResult:
    """Merge ``results`` (one per superelement of ``rows``, read on their own) into one."""
    from ada.fem import FemSet
    from ada.fem.results.common import ElementBlock, FEAResult, FemNodes, Mesh

    bases = id_bases(rows, results)
    coords, node_ids, node_labels = [], [], []
    blocks, sections, materials, vectors, sets, eccentricities, elem_data = [], {}, {}, {}, {}, {}, []
    blocks_by_part: list[list] = []
    merged: dict[tuple, list] = {}
    groups: list[dict] = []
    node_count = 0
    index_record = []

    for row, result, base in zip(rows, results, bases):
        transform = placement.get(row.ihref, np.eye(4))
        rotation, translation = transform[:3, :3], transform[:3, 3]
        turned = not np.allclose(rotation, np.eye(3))
        mesh = result.mesh

        xyz = np.asarray(mesh.nodes.coords, dtype=float)
        coords.append(xyz @ rotation.T + translation)
        node_ids.append(np.asarray(mesh.nodes.identifiers, dtype=np.int64) + base)
        node_labels.append(np.asarray(mesh.nodes.shown_ids(), dtype=np.int64) + base)

        blocks_by_part.append([])
        for block in mesh.elements:
            refs = np.asarray(block.node_refs)
            refs = refs + (node_count if block.node_refs_are_indices else base)
            blocks_by_part[-1].append(
                ElementBlock(
                    elem_info=block.elem_info,
                    node_refs=refs,
                    identifiers=np.asarray(block.identifiers, dtype=np.int64) + base,
                    node_refs_are_indices=block.node_refs_are_indices,
                    labels=np.asarray(block.shown_ids(), dtype=np.int64) + base,
                )
            )
        blocks.extend(blocks_by_part[-1])
        node_count += len(xyz)

        sections.update(_offset_keys(mesh.sections, base))
        materials.update(_offset_keys(mesh.materials, base))
        for key, vec in (mesh.vectors or {}).items():
            vectors[int(key) + base] = list(rotation @ np.asarray(vec[:3], dtype=float)) + list(vec[3:])
        if mesh.elem_data is not None and len(mesh.elem_data):
            data = np.asarray(mesh.elem_data, dtype=float).copy()
            data[:, 0] += base
            for col in (1, 2, 3):
                data[:, col] = _offset_ref(data[:, col], base)
            elem_data.append(data)
        for elno, per_node in (mesh.eccentricities or {}).items():
            eccentricities[int(elno) + base] = [None if v is None else rotation @ np.asarray(v) for v in per_node]
        for set_name, fs in (mesh.sets or {}).items():
            members = [int(getattr(m, "id", m)) + base for m in fs.members]
            sets[f"{row.label}: {set_name}"] = FemSet(f"{row.label}: {set_name}", members, set_type=fs.type)

        for group in getattr(result, "sesam_groups", None) or []:
            prefix = "EL" if group.get("fe_object_type") == "element" else "P"
            groups.append(
                {
                    **group,
                    "name": f"{row.label}: {group['name']}",
                    "members": [f"{prefix}{int(str(m)[len(prefix):]) + base}" for m in group["members"]],
                }
            )

        for field in result.results:
            values = np.asarray(field.values, dtype=float).copy()
            values[:, 0] += base
            if turned and isinstance(field, NodalFieldData):
                values = _rotate_rows(values, list(field.components or []), rotation, first_col=1)
            changes = {"values": values}
            if field.name in (MATERIAL_FIELD, SECTION_FIELD):
                # The value IS a material / section id: prefixed like every other id, and so
                # are the legend's labels for it.
                values[:, 2] = _offset_ref(values[:, 2], base)
                if field.presentation is not None:
                    labels = tuple((v + base if v > 0 else v, text) for v, text in field.presentation.value_labels)
                    changes["presentation"] = dataclasses.replace(field.presentation, value_labels=labels)
            et = getattr(field, "elem_type", None)
            pos = getattr(field, "field_pos", None)
            key = (type(field).__name__, field.name, int(field.step), str(et), str(pos))
            merged.setdefault(key, []).append(dataclasses.replace(field, **changes))

        index_record.append({"label": row.label, "id_base": base, "placement": transform.ravel().tolist()})

    fields = [_merge_parts(parts) for parts in merged.values()]
    fields += _super_element_fields(rows, blocks_by_part, fields)

    mesh = Mesh(
        elements=blocks,
        nodes=FemNodes(
            coords=np.vstack(coords), identifiers=np.concatenate(node_ids), labels=np.concatenate(node_labels)
        ),
        sections=sections or None,
        materials=materials or None,
        vectors=vectors or None,
        elem_data=np.vstack(elem_data) if elem_data else None,
        sets=sets or None,
        eccentricities=eccentricities or None,
    )
    first = results[0]
    out = FEAResult(
        name=name,
        software=first.software,
        results=fields,
        mesh=mesh,
        results_file_path=results_file_path or first.results_file_path,
        step_name_map=first.step_name_map,
        description=first.description,
        software_version=first.software_version,
        eigen_mode_data=first.eigen_mode_data,
        analysis_kind=first.analysis_kind,
    )
    out.sesam_groups = groups or None
    out.sesam_case_names = getattr(first, "sesam_case_names", None)
    out.sesam_result_cases = getattr(first, "sesam_result_cases", None)
    out.sesam_super_elements = index_record
    return out

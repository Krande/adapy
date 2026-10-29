"""A Sesam superelement assembly as one result: every superelement placed and merged.

A SIN written for a superelement assembly holds one RESULTS entry per superelement, each
with its own mesh, numbering and result tables, plus the hierarchy that places them
(HSUPTRAN). :class:`~.sin_field_stream.SinFieldStream` reads each superelement on its own,
one result case at a time; this merges them into one :class:`FEAResult` -- what Xtract
shows when it opens the assembly.

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

__all__ = ["assemble_meshes", "placements", "id_bases"]

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


def _offset_keys(d: dict | None, base: int) -> dict:
    return {int(k) + base: v for k, v in (d or {}).items()}


def _offset_ref(col: np.ndarray, base: int) -> np.ndarray:
    """``col`` + ``base`` where it references something (> 0); 0 / -1 stay "none"."""
    col = np.asarray(col, dtype=float)
    return np.where(col > 0, col + base, col)


@dataclasses.dataclass
class _FieldPart:
    """One superelement's rows of a field, with what places them in the assembly."""

    field: object
    base: int
    rotation: np.ndarray | None  # None when the superelement is not turned


def _offset_presentation(field, base: int):
    """``field``'s presentation with its material / section legend values prefixed by ``base``."""
    if field.name not in (MATERIAL_FIELD, SECTION_FIELD) or field.presentation is None:
        return field.presentation
    labels = tuple((v + base if v > 0 else v, text) for v, text in field.presentation.value_labels)
    return dataclasses.replace(field.presentation, value_labels=labels)


def _place_rows(out: np.ndarray, part: _FieldPart) -> None:
    """Prefix ids and turn vectors of ``out`` (the part's rows, already copied in) in place."""
    field = part.field
    out[:, 0] += part.base
    if part.rotation is not None and isinstance(field, NodalFieldData):
        names = list(field.components or [])
        for triplet in _VECTOR_TRIPLETS:
            if all(c in names for c in triplet):
                cols = [1 + names.index(c) for c in triplet]
                out[:, cols] = out[:, cols] @ part.rotation.T
    if field.name in (MATERIAL_FIELD, SECTION_FIELD):
        # The value IS a material / section id: prefixed like every other id.
        out[:, 2] = _offset_ref(out[:, 2], part.base)


def _merge_parts(parts: list[_FieldPart]):
    """One field from the superelements' parts of it: their rows together, and (for a
    categorical property) every part's legend labels.

    The merged array is allocated once and filled part by part. Each part is dropped from
    ``parts`` once copied, so its array can be freed before the next is copied.
    """
    first = parts[0].field
    presentations = [_offset_presentation(p.field, p.base) for p in parts]
    widths = {np.shape(p.field.values)[1] for p in parts}
    if len(widths) != 1:
        raise ValueError(f"{first.name}: superelements store {sorted(widths)} columns")
    rows = sum(len(p.field.values) for p in parts)
    values = np.empty((rows, widths.pop()), dtype=float)

    start = 0
    for i in range(len(parts)):
        part = parts[i]
        n = len(part.field.values)
        block = values[start : start + n]
        block[...] = part.field.values
        _place_rows(block, part)
        start += n
        parts[i] = part = None

    presentation = presentations[0]
    if len(presentations) > 1 and presentation is not None and any(p and p.value_labels for p in presentations):
        labels = {}
        for p in presentations:
            for value, text in p.value_labels if p else ():
                labels.setdefault(value, text)
        presentation = dataclasses.replace(presentation, value_labels=tuple(sorted(labels.items())))
    return dataclasses.replace(first, values=values, presentation=presentation)


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


@dataclasses.dataclass
class AssembledMesh:
    """The superelements' meshes placed and merged, and what places each one's fields."""

    #: The merged mesh and the result's metadata, without fields.
    result: FEAResult
    rows: list
    bases: list[int]
    #: Per superelement, its rotation, or None when it is not turned.
    rotations: list
    blocks_by_part: list[list]

    def merge_fields(self, fields_per_part: list[list]) -> list:
        """One field per name, step and type from each superelement's ``fields_per_part``.

        The lists are emptied as they are taken, so each part is freed once merged.
        """
        merged: dict[tuple, list] = {}
        for fields, base, rotation in zip(fields_per_part, self.bases, self.rotations):
            for field in fields:
                et = getattr(field, "elem_type", None)
                pos = getattr(field, "field_pos", None)
                key = (type(field).__name__, field.name, int(field.step), str(et), str(pos))
                merged.setdefault(key, []).append(_FieldPart(field, base, rotation))
            fields.clear()
        return [_merge_parts(merged.pop(key)) for key in list(merged)]

    def super_element_fields(self, fields) -> list:
        """``props.super_element`` on the step ``fields`` puts the property fields on."""
        return _super_element_fields(self.rows, self.blocks_by_part, fields)


def assemble_meshes(
    rows: list[SuperElementInfo],
    results: list[FEAResult],
    placement: dict[int, np.ndarray],
    name: str,
    results_file_path: pathlib.Path | None = None,
) -> AssembledMesh:
    """The meshes of ``results`` (one per superelement of ``rows``) placed and merged."""
    from ada.fem import FemSet
    from ada.fem.results.common import ElementBlock, FEAResult, FemNodes, Mesh

    bases = id_bases(rows, results)
    coords, node_ids, node_labels = [], [], []
    blocks, sections, materials, vectors, sets, eccentricities, elem_data = [], {}, {}, {}, {}, {}, []
    blocks_by_part: list[list] = []
    rotations = []
    groups: list[dict] = []
    node_count = 0
    index_record = []

    for row, result, base in zip(rows, results, bases):
        transform = placement.get(row.ihref, np.eye(4))
        rotation, translation = transform[:3, :3], transform[:3, 3]
        rotations.append(None if np.allclose(rotation, np.eye(3)) else rotation)
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
            sets[f"{row.label}: {set_name}"] = FemSet(
                f"{row.label}: {set_name}", members, set_type=fs.type, role=getattr(fs, "role", None)
            )

        for group in getattr(result, "sesam_groups", None) or []:
            prefix = "EL" if group.get("fe_object_type") == "element" else "P"
            groups.append(
                {
                    **group,
                    "name": f"{row.label}: {group['name']}",
                    "members": [f"{prefix}{int(str(m)[len(prefix) :]) + base}" for m in group["members"]],
                }
            )

        index_record.append({"label": row.label, "id_base": base, "placement": transform.ravel().tolist()})

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
        results=[],
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
    return AssembledMesh(out, list(rows), bases, rotations, blocks_by_part)

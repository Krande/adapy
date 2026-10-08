"""Turn OpenCourant animation states into an adapy :class:`FEAResult`.

Every animation file is one time state. The first state (t = 0) defines the
reference mesh; per state we emit

* ``U`` — nodal displacement (the ``/ANIM/VECT/DISP`` vector when written, otherwise
  the coordinate change against the first state), tagged so the viewer warps by it;
* the other nodal vectors (velocity, contact force, ...) and nodal scalars;
* every element scalar (``/ANIM/ELEM/...``: von Mises, plastic strain, energy ...)
  as one value per element, split per element shape.

Times are the field ``step`` values, so the viewer's step picker reads in seconds.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from ada.fem.formats.general import FEATypes
from ada.fem.results.common import ElementBlock, ElementInfo, FEAResult, FemNodes, Mesh
from ada.fem.results.field_data import (
    ElementFieldData,
    FieldPosition,
    NodalFieldData,
    NodalFieldType,
)
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes

from .read_anim import AnimElementBlock, AnimFrame

#: Animation vector names (as the engine writes them) -> (adapy field name, field type).
_VECTOR_NAMES = {
    "displacement": ("U", NodalFieldType.DISP),
    "velocity": ("V", NodalFieldType.VEL),
    "contact force": ("CF", NodalFieldType.UNKNOWN),
}


def field_name(name: str) -> str:
    """Solver labels ("Von Mises", "Plastic Strain") become storage-safe names (blob file names)."""
    return "_".join(name.split())


def _vector_field(name: str) -> tuple[str, NodalFieldType]:
    key = name.strip().lower()
    for token, out in _VECTOR_NAMES.items():
        if token in key:
            return out
    return field_name(name), NodalFieldType.UNKNOWN


def _shell_split(block: AnimElementBlock) -> dict[ShellShapes, np.ndarray]:
    """Row masks of the triangles (3rd node repeated) and quads of a 2D block."""
    conn = block.connectivity
    tri = conn[:, 2] == conn[:, 3]
    return {ShellShapes.TRI: tri, ShellShapes.QUAD: ~tri}


def _element_ids(block: AnimElementBlock, offset: int) -> np.ndarray:
    if block.element_ids is not None:
        return np.asarray(block.element_ids, dtype=np.int64)
    return np.arange(offset + 1, offset + block.num_elements + 1, dtype=np.int64)


def build_mesh(frame: AnimFrame) -> tuple[Mesh, dict]:
    """Reference mesh from one state plus the per-shape row masks / element ids for fields."""
    node_ids = (
        np.asarray(frame.node_ids, dtype=np.int64)
        if frame.node_ids is not None
        else np.arange(1, frame.num_nodes + 1, dtype=np.int64)
    )
    nodes = FemNodes(coords=np.asarray(frame.coords, dtype=float), identifiers=node_ids)
    blocks: list[ElementBlock] = []
    layout: dict = {"shell": {}, "solid": None, "line": None}

    shells = frame.shells
    if shells.num_elements:
        ids = _element_ids(shells, 0)
        for shape, mask in _shell_split(shells).items():
            if not mask.any():
                continue
            conn = shells.connectivity[mask][:, :3] if shape == ShellShapes.TRI else shells.connectivity[mask]
            blocks.append(
                ElementBlock(
                    elem_info=ElementInfo(shape, FEATypes.OPENCOURANT, "SH3N" if shape == ShellShapes.TRI else "SHELL"),
                    node_refs=node_ids[conn],
                    identifiers=ids[mask],
                )
            )
            layout["shell"][shape] = (mask, ids[mask])

    offset = shells.num_elements
    if frame.solids is not None and frame.solids.num_elements:
        ids = _element_ids(frame.solids, offset)
        blocks.append(
            ElementBlock(
                elem_info=ElementInfo(SolidShapes.HEX8, FEATypes.OPENCOURANT, "BRICK"),
                node_refs=node_ids[frame.solids.connectivity],
                identifiers=ids,
            )
        )
        layout["solid"] = ids
        offset += frame.solids.num_elements

    if frame.lines is not None and frame.lines.num_elements:
        ids = _element_ids(frame.lines, offset)
        blocks.append(
            ElementBlock(
                elem_info=ElementInfo(LineShapes.LINE, FEATypes.OPENCOURANT, "LINE"),
                node_refs=node_ids[frame.lines.connectivity],
                identifiers=ids,
            )
        )
        layout["line"] = ids

    return Mesh(elements=blocks, nodes=nodes), layout


def _nodal(name, t, node_ids, values, components, field_type=None) -> NodalFieldData:
    vals = np.column_stack([node_ids, np.asarray(values, dtype=float).reshape(len(node_ids), -1)])
    return NodalFieldData(name=name, step=t, components=components, values=vals, field_type=field_type)


def _element(name, t, ids, values, shape) -> ElementFieldData:
    vals = np.column_stack([ids, np.ones(len(ids)), np.asarray(values, dtype=float)])
    return ElementFieldData(
        name=name,
        step=t,
        components=[name],
        values=vals,
        field_pos=FieldPosition.ELEMENT_AVERAGE,
        elem_type=shape,
    )


def frame_fields(frame: AnimFrame, ref: AnimFrame, node_ids: np.ndarray, layout: dict) -> list:
    t = float(frame.time)
    out = []
    vectors = dict(frame.vectors)
    disp_key = next((k for k in vectors if _vector_field(k)[1] == NodalFieldType.DISP), None)
    disp = vectors.pop(disp_key) if disp_key is not None else frame.coords - ref.coords
    out.append(_nodal("U", t, node_ids, disp, ["U1", "U2", "U3"], NodalFieldType.DISP))
    for name, vec in vectors.items():
        fname, ftype = _vector_field(name)
        out.append(_nodal(fname, t, node_ids, vec, [f"{fname}1", f"{fname}2", f"{fname}3"], ftype))
    for name, vals in frame.nodal_scalars.items():
        out.append(_nodal(field_name(name), t, node_ids, vals, [field_name(name)]))

    for name, vals in frame.shells.scalars.items():
        for shape, (mask, ids) in layout["shell"].items():
            out.append(_element(field_name(name), t, ids, vals[mask], shape))
    if layout["solid"] is not None:
        for name, vals in frame.solids.scalars.items():
            out.append(_element(field_name(name), t, layout["solid"], vals, SolidShapes.HEX8))
    if layout["line"] is not None:
        for name, vals in frame.lines.scalars.items():
            out.append(_element(field_name(name), t, layout["line"], vals, LineShapes.LINE))
    return out


def fea_result_from_frames(frames: Iterable[AnimFrame], name: str, results_file_path=None) -> FEAResult:
    frames = iter(frames)
    try:
        ref = next(frames)
    except StopIteration as e:
        raise ValueError("no animation states to read") from e
    mesh, layout = build_mesh(ref)
    node_ids = mesh.nodes.identifiers
    results = frame_fields(ref, ref, node_ids, layout)
    times = [float(ref.time)]
    for frame in frames:
        if frame.num_nodes != ref.num_nodes:
            raise ValueError(f"state t={frame.time} has {frame.num_nodes} nodes, the first has {ref.num_nodes}")
        results += frame_fields(frame, ref, node_ids, layout)
        times.append(float(frame.time))
    step_names = {t: f"t = {t:.4g} s" for t in times}
    return FEAResult(
        name=name,
        software=FEATypes.OPENCOURANT,
        results=results,
        mesh=mesh,
        results_file_path=results_file_path,
        step_name_map=step_names,
        description="OpenCourant explicit dynamics (animation states)",
        analysis_kind="transient",
    )

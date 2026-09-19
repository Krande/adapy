"""The compact beam-solids artefact (AFBS): its collector, writer, reader and
reference expander.

:func:`~.beam_solids.tessellate_beams_to_solid_mesh` bakes what the viewer
eventually draws — a vertex buffer, an index buffer and a per-vertex warp
triple. On a large deck that is an 88 MB GLB plus a 32 MB AFBV sidecar, and
every byte of it is derivable: ``extrude_outline`` is two matrix multiplies
over a per-SECTION outline, the triangle list belongs to the section too, and
the only per-beam input is a :class:`~ada.api.beams.geom_beams.BeamFrame`. A
deck with 165k beams has on the order of ten distinct outlines.

So this module ships the generators rather than the result: the outline table
once, then 56 bytes per beam, and the browser expands them in a worker into
exactly the buffers the GLB path produced. :func:`expand_beam_solid_instances`
is the reference implementation of that expansion — the parity test measures
it against ``tessellate_beams_to_solid_mesh(method="procedural")``, and the
frontend's TypeScript expander against a fixture this one generates.

**Only prismatic beams travel in AFBS.** The format has no room for a beam
whose section changes along its length or that has had a boolean cut out of
it; those are exactly the beams the procedural extruder hands to OCC, and OCC
produces a one-off mesh with no generator to ship. They are counted under
``compact-unsupported[<reason>]`` and dropped, so a tapered or boolean beam
renders as a line (the viewer's existing fallback for a beam with no solid).
That is the price of the format and it is a deliberate one: on real decks the
fallbacks are a fraction of a percent, and ``beam_solid_format="mesh"`` still
bakes every beam the old way.

Everything here is written to be reproducible in JavaScript float64 to the
bit: no ``matmul``, no ``dot``, no reductions — just elementwise multiplies
and adds in a fixed order, promoted to float64 from the float32 the file
carries, and rounded back to float32 on the way out. BLAS is free to
re-associate and to fuse multiply-add; the browser is not, so the two would
disagree in the last bit of a position and the parity test would be a
tolerance instead of an equality.
"""

from __future__ import annotations

import os
import pathlib
import struct
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dc_field

import numpy as np

from .formats import (
    BEAM_COMPACT_BEAM_BYTES,
    BEAM_COMPACT_HEADER_BYTES,
    BEAM_COMPACT_MAGIC,
    BEAM_COMPACT_VERSION,
)
from .specs import SolidBeamMesh

# What ``bake_artefacts(beam_solid_format=...)`` accepts. ``"compact"`` writes
# the single AFBS file; ``"mesh"`` writes the GLB + AFBV + AFEM trio, which is
# every beam including the ones AFBS has no room for.
BEAM_SOLID_FORMATS = ("compact", "mesh")

__all__ = [
    "BEAM_SOLID_FORMATS",
    "CompactSection",
    "BeamSolidInstances",
    "collect_beam_solid_instances",
    "expand_beam_solid_instances",
    "write_beam_solids_compact",
    "read_beam_solids_compact",
]


@dataclass(frozen=True)
class CompactSection:
    """One section's outline as AFBS carries it: sample points and the
    triangle list of its extrusion.

    Deliberately NOT a :class:`~.beam_extrude.SectionOutline`. That record
    also holds ring slices, the cap triangulation, the centroid and the area,
    none of which survives a round trip through the file because none of it is
    needed to expand a beam — and a reader that had to invent them would be
    handing callers a SectionOutline that lies about four of its six fields.

    ``triangles`` indexes the ``2 * n_points`` vertices of one extrusion:
    outline point ``k`` is vertex ``k`` at the near end and ``k + n_points``
    at the far end, for every beam that shares the section.
    """

    points: np.ndarray  # (n, 2) float32, (u, v) in the profile plane
    triangles: np.ndarray  # (m, 3) uint32 into the 2n extruded vertices

    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])

    @property
    def n_triangles(self) -> int:
        return int(self.triangles.shape[0])


@dataclass
class BeamSolidInstances:
    """Every prismatic beam of a bake as a section reference plus a frame.

    The arrays are parallel, one entry per beam, in the order the beams were
    collected — which is the order their draw ranges tile the expanded
    triangle buffer, so the reader, the writer and both expanders agree on
    element order without storing it.

    ``node0`` / ``node1`` are 0-based rows of the bake's main point buffer,
    exactly as in :class:`~.specs.SolidBeamMesh`. They are per BEAM here
    rather than per vertex because that is what they are: every vertex of a
    beam carries the same pair.

    ``total_beams`` and ``skip_reasons`` are the same coverage telemetry the
    mesh path reports, so the manifest block is identical whichever format
    was baked.
    """

    sections: list[CompactSection]
    label: np.ndarray  # (n,) uint32 — the source line-element id
    section_idx: np.ndarray  # (n,) uint32 — into ``sections``
    node0: np.ndarray  # (n,) uint32
    node1: np.ndarray  # (n,) uint32
    origin: np.ndarray  # (n, 3) float32 — BeamFrame.origin
    xvec: np.ndarray  # (n, 3) float32 — extrusion direction
    yvec: np.ndarray  # (n, 3) float32 — profile local +y
    length: np.ndarray  # (n,) float32
    total_beams: int = 0
    skip_reasons: dict = dc_field(default_factory=dict)

    @property
    def n_beams(self) -> int:
        return int(self.label.shape[0])

    @property
    def n_verts(self) -> int:
        """Vertices the expansion will produce — two rings per beam."""

        if self.n_beams == 0:
            return 0
        per_section = np.asarray([s.n_points for s in self.sections], dtype=np.int64)
        return int(2 * per_section[self.section_idx.astype(np.int64)].sum())

    @property
    def n_triangles(self) -> int:
        if self.n_beams == 0:
            return 0
        per_section = np.asarray([s.n_triangles for s in self.sections], dtype=np.int64)
        return int(per_section[self.section_idx.astype(np.int64)].sum())


def _as_f32(values) -> np.ndarray:
    """Round to float32 and keep it there.

    Collection quantises up front rather than at write time so that
    ``expand(collect(...))`` and ``expand(read(write(collect(...))))`` are the
    same arrays down to the last bit. Anything else would leave the parity
    test asserting on a file round trip that quietly changes the answer.
    """

    return np.ascontiguousarray(values, dtype=np.float32)


def collect_beam_solid_instances(
    beams,
    *,
    extra_skip_reasons: dict | None = None,
    total_beams: int | None = None,
    deflection: float | None = None,
    max_angle: float | None = None,
) -> "BeamSolidInstances | None":
    """Reduce a bake's beams to an outline table plus one frame each.

    ``beams`` is the same iterable of
    ``(beam, elem_id, n0_idx, n1_idx, n0_pos, n1_pos)`` tuples
    :func:`~.beam_solids.tessellate_beams_to_solid_mesh` takes, and the loop
    is deliberately the same shape as its procedural branch: the same
    :func:`~.beam_extrude.unsupported_reason` gate, the same
    :class:`~.beam_extrude.SectionOutlineCache`, the same
    :func:`~ada.api.beams.geom_beams.straight_beam_frame`. The endpoint
    POSITIONS are unused here — ``t`` is a per-vertex quantity the expander
    computes from the node positions it already has.

    A beam the extruder cannot take is DROPPED and counted under
    ``compact-unsupported[<reason>]`` — see the module docstring; the mesh
    path's ``occ-fallback[...]`` bucket has no counterpart here because there
    is no fallback to fall back to. Section indices are assigned in order of
    first use.

    Returns ``None`` when no beam survived, matching
    ``tessellate_beams_to_solid_mesh``'s contract: the bake then writes no
    beam-solid artefact and the manifest carries no URL.
    """

    import time

    from ada.api.beams.geom_beams import straight_beam_frame
    from ada.config import get_logger

    from .beam_extrude import (
        DEFAULT_DEFLECTION,
        DEFAULT_MAX_ANGLE,
        SectionOutlineCache,
        unsupported_reason,
    )

    t_start = time.perf_counter()
    outlines = SectionOutlineCache(
        deflection=DEFAULT_DEFLECTION if deflection is None else deflection,
        max_angle=DEFAULT_MAX_ANGLE if max_angle is None else max_angle,
    )

    sections: list[CompactSection] = []
    # id(section) -> (section, index). The section object is kept in the value
    # for the same reason SectionOutlineCache keeps it: an id is only unique
    # while the object it names is alive.
    section_index: dict[int, tuple[object, int]] = {}

    labels: list[int] = []
    sec_idx: list[int] = []
    n0s: list[int] = []
    n1s: list[int] = []
    origins: list[np.ndarray] = []
    xvecs: list[np.ndarray] = []
    yvecs: list[np.ndarray] = []
    lengths: list[float] = []

    skip_reasons: dict[str, int] = defaultdict(int)
    if extra_skip_reasons:
        for k, v in extra_skip_reasons.items():
            skip_reasons[k] += int(v)

    beams = list(beams)
    if total_beams is None:
        total_beams = len(beams) + sum(skip_reasons.values())

    for beam, elem_id, n0_idx, n1_idx, _n0_pos, _n1_pos in beams:
        reason = unsupported_reason(beam)
        outline = outlines.get(beam.section) if reason is None else None
        if reason is None and outline is None:
            reason = "outline"
        if reason is not None:
            skip_reasons[f"compact-unsupported[{reason}]"] += 1
            continue

        try:
            frame = straight_beam_frame(beam)
        except Exception as e:  # noqa: BLE001 — one bad beam must not fail the bake
            skip_reasons[f"error[{type(e).__name__}]"] += 1
            get_logger().debug("beam-solid frame failure elem %s: %s", elem_id, e)
            continue

        key = id(beam.section)
        hit = section_index.get(key)
        if hit is None:
            hit = (beam.section, len(sections))
            section_index[key] = hit
            sections.append(
                CompactSection(
                    points=_as_f32(outline.points),
                    triangles=np.ascontiguousarray(outline.triangles, dtype=np.uint32),
                )
            )

        labels.append(int(elem_id))
        sec_idx.append(hit[1])
        n0s.append(int(n0_idx))
        n1s.append(int(n1_idx))
        origins.append(frame.origin)
        xvecs.append(frame.xvec)
        yvecs.append(frame.yvec)
        lengths.append(float(frame.length))

    if total_beams:
        skip_summary = ", ".join(f"{k}={v}" for k, v in sorted(skip_reasons.items())) or "none"
        get_logger().info(
            "beam-solid coverage: %d of %d beams on %d section outlines via compact in %.2fs (reasons: %s)",
            len(labels),
            total_beams,
            len(sections),
            time.perf_counter() - t_start,
            skip_summary,
        )

    if not labels:
        return None

    empty3 = np.empty((0, 3), dtype=np.float32)
    return BeamSolidInstances(
        sections=sections,
        label=np.asarray(labels, dtype=np.uint32),
        section_idx=np.asarray(sec_idx, dtype=np.uint32),
        node0=np.asarray(n0s, dtype=np.uint32),
        node1=np.asarray(n1s, dtype=np.uint32),
        origin=_as_f32(np.stack(origins, axis=0) if origins else empty3),
        xvec=_as_f32(np.stack(xvecs, axis=0) if xvecs else empty3),
        yvec=_as_f32(np.stack(yvecs, axis=0) if yvecs else empty3),
        length=_as_f32(np.asarray(lengths, dtype=np.float64)),
        total_beams=int(total_beams),
        skip_reasons=dict(skip_reasons),
    )


def expand_beam_solid_instances(inst: BeamSolidInstances, points) -> SolidBeamMesh:
    """Expand instances back into the mesh the GLB path would have baked.

    ``points`` is the bake's main point buffer — the same ``(n_points, 3)``
    array ``fea.mesh.glb`` carries, which is what the frontend has after the
    mesh loads. ``node0`` / ``node1`` index into it, and the axial parameter
    ``t`` is measured against those NODE positions, not against the extrusion
    axis: when a beam's two ends carry different eccentricities the frame axis
    tilts away from the element axis, so ``t`` varies within a ring. That is
    why it is computed here (and in the browser) instead of shipped.

    This is the reference for the TypeScript expander, so the arithmetic is
    written the way JavaScript will do it — see the module docstring.
    """

    from ada.visit.rendering.femviz import ElementRange

    pts_main = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    n_beams = inst.n_beams

    positions = np.empty((inst.n_verts, 3), dtype=np.float32)
    triangles = np.empty((inst.n_triangles, 3), dtype=np.uint32)
    vertex_node0 = np.empty(inst.n_verts, dtype=np.uint32)
    vertex_node1 = np.empty(inst.n_verts, dtype=np.uint32)
    vertex_t = np.empty(inst.n_verts, dtype=np.float32)
    ranges: list = []

    vertex_offset = 0
    tri_cursor = 0
    for k in range(n_beams):
        sec = inst.sections[int(inst.section_idx[k])]
        n = sec.n_points
        uv = np.asarray(sec.points, dtype=np.float64)

        ox, oy, oz = (float(v) for v in inst.origin[k])
        xx, xy, xz = (float(v) for v in inst.xvec[k])
        yx, yy, yz = (float(v) for v in inst.yvec[k])
        length = float(inst.length[k])

        # up = normalize(cross(xvec, yvec)) — BeamFrame's own construction.
        ux = xy * yz - xz * yy
        uy = xz * yx - xx * yz
        uz = xx * yy - xy * yx
        un = np.sqrt(ux * ux + uy * uy + uz * uz)
        if un > 0.0:
            ux, uy, uz = ux / un, uy / un, uz / un

        u = uv[:, 0]
        v = uv[:, 1]
        verts = np.empty((2 * n, 3), dtype=np.float64)
        # (u, v) -> origin + u*yvec + v*up, exactly extrude_outline's mapping.
        verts[:n, 0] = u * yx + v * ux + ox
        verts[:n, 1] = u * yy + v * uy + oy
        verts[:n, 2] = u * yz + v * uz + oz
        verts[n:, 0] = verts[:n, 0] + length * xx
        verts[n:, 1] = verts[:n, 1] + length * xy
        verts[n:, 2] = verts[:n, 2] + length * xz

        # Round to float32 BEFORE measuring t: the browser has no other
        # positions to measure against, so the reference must not either.
        vf = verts.astype(np.float32)
        positions[vertex_offset : vertex_offset + 2 * n] = vf

        i0 = int(inst.node0[k])
        i1 = int(inst.node1[k])
        p0 = pts_main[i0]
        p1 = pts_main[i1]
        ax = float(p1[0]) - float(p0[0])
        ay = float(p1[1]) - float(p0[1])
        az = float(p1[2]) - float(p0[2])
        axis_sq = ax * ax + ay * ay + az * az
        if axis_sq <= 0.0:
            # Zero-length beam: every vertex at t=0, so the warp lerp collapses
            # onto disp[node0] instead of dividing by nothing.
            t_vals = np.zeros(2 * n, dtype=np.float32)
        else:
            w = np.asarray(vf, dtype=np.float64)
            rel_x = w[:, 0] - float(p0[0])
            rel_y = w[:, 1] - float(p0[1])
            rel_z = w[:, 2] - float(p0[2])
            t_vals = ((rel_x * ax + rel_y * ay + rel_z * az) / axis_sq).clip(0.0, 1.0).astype(np.float32)
        vertex_t[vertex_offset : vertex_offset + 2 * n] = t_vals
        vertex_node0[vertex_offset : vertex_offset + 2 * n] = i0
        vertex_node1[vertex_offset : vertex_offset + 2 * n] = i1

        tri_count = sec.n_triangles
        triangles[tri_cursor : tri_cursor + tri_count] = sec.triangles + np.uint32(vertex_offset)
        ranges.append(ElementRange(label=int(inst.label[k]), tri_start=tri_cursor, tri_count=tri_count))

        vertex_offset += 2 * n
        tri_cursor += tri_count

    return SolidBeamMesh(
        points=positions,
        triangles=triangles,
        element_ranges=ranges,
        vertex_node0=vertex_node0,
        vertex_node1=vertex_node1,
        vertex_t=vertex_t,
        total_beams=inst.total_beams,
        skip_reasons=dict(inst.skip_reasons),
    )


# ---------------------------------------------------------------------------
# AFBS reader / writer
# ---------------------------------------------------------------------------


def write_beam_solids_compact(inst: BeamSolidInstances, out_path: os.PathLike) -> tuple[int, int]:
    """Write the AFBS artefact. Returns ``(n_beams, n_verts)``.

    ``n_verts`` is what the expansion will produce, which is the count the
    manifest advertises as ``n_beam_solid_verts`` — the frontend checks the
    warp arrays against it exactly as it checked the AFBV row count against
    the GLB's vertex count.
    """

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_sections = len(inst.sections)
    n_beams = inst.n_beams

    with open(out_path, "wb") as f:
        f.write(BEAM_COMPACT_MAGIC + struct.pack("<III", BEAM_COMPACT_VERSION, n_sections, n_beams))
        for sec in inst.sections:
            f.write(struct.pack("<II", sec.n_points, sec.n_triangles))
            f.write(np.ascontiguousarray(sec.points, dtype=np.float32).tobytes(order="C"))
            f.write(np.ascontiguousarray(sec.triangles, dtype=np.uint32).tobytes(order="C"))
        if n_beams:
            # One interleaved record per beam so the browser reads a beam with
            # one stride rather than seven strided passes over the file.
            record = np.empty((n_beams, BEAM_COMPACT_BEAM_BYTES // 4), dtype=np.uint32)
            record[:, 0] = np.asarray(inst.label, dtype=np.uint32)
            record[:, 1] = np.asarray(inst.section_idx, dtype=np.uint32)
            record[:, 2] = np.asarray(inst.node0, dtype=np.uint32)
            record[:, 3] = np.asarray(inst.node1, dtype=np.uint32)
            # float32 bits into the uint32 slots — a view-cast, not a convert.
            record[:, 4:7] = np.ascontiguousarray(inst.origin, dtype=np.float32).view(np.uint32)
            record[:, 7:10] = np.ascontiguousarray(inst.xvec, dtype=np.float32).view(np.uint32)
            record[:, 10:13] = np.ascontiguousarray(inst.yvec, dtype=np.float32).view(np.uint32)
            record[:, 13] = np.ascontiguousarray(inst.length, dtype=np.float32).view(np.uint32)
            f.write(record.tobytes(order="C"))

    return n_beams, inst.n_verts


def read_beam_solids_compact(path: os.PathLike) -> BeamSolidInstances:
    """Read an AFBS artefact back into instances.

    ``total_beams`` comes back as the number of beams the file carries and
    ``skip_reasons`` empty: the coverage telemetry lives in the manifest, not
    in the binary, because it is about beams that are NOT in the file.
    """

    data = pathlib.Path(path).read_bytes()
    if len(data) < BEAM_COMPACT_HEADER_BYTES:
        raise ValueError(f"beam-solid compact: too small ({len(data)} bytes)")
    if data[:4] != BEAM_COMPACT_MAGIC:
        raise ValueError(f"beam-solid compact: bad magic {data[:4]!r}")
    version, n_sections, n_beams = struct.unpack("<III", data[4:16])
    if version != BEAM_COMPACT_VERSION:
        raise ValueError(f"beam-solid compact: version {version}, expected {BEAM_COMPACT_VERSION}")

    cursor = BEAM_COMPACT_HEADER_BYTES
    sections: list[CompactSection] = []
    for _ in range(n_sections):
        if cursor + 8 > len(data):
            raise ValueError("beam-solid compact: truncated section header")
        n_points, n_tris = struct.unpack("<II", data[cursor : cursor + 8])
        cursor += 8
        pts_bytes = n_points * 2 * 4
        tri_bytes = n_tris * 3 * 4
        if cursor + pts_bytes + tri_bytes > len(data):
            raise ValueError("beam-solid compact: truncated section payload")
        pts = np.frombuffer(data, dtype=np.float32, count=n_points * 2, offset=cursor).reshape(n_points, 2)
        cursor += pts_bytes
        tris = np.frombuffer(data, dtype=np.uint32, count=n_tris * 3, offset=cursor).reshape(n_tris, 3)
        cursor += tri_bytes
        sections.append(CompactSection(points=np.ascontiguousarray(pts), triangles=np.ascontiguousarray(tris)))

    n_words = BEAM_COMPACT_BEAM_BYTES // 4
    if cursor + n_beams * BEAM_COMPACT_BEAM_BYTES > len(data):
        raise ValueError("beam-solid compact: truncated beam table")
    record = np.frombuffer(data, dtype=np.uint32, count=n_beams * n_words, offset=cursor).reshape(n_beams, n_words)
    floats = record.view(np.float32)

    return BeamSolidInstances(
        sections=sections,
        label=np.ascontiguousarray(record[:, 0]),
        section_idx=np.ascontiguousarray(record[:, 1]),
        node0=np.ascontiguousarray(record[:, 2]),
        node1=np.ascontiguousarray(record[:, 3]),
        origin=np.ascontiguousarray(floats[:, 4:7]),
        xvec=np.ascontiguousarray(floats[:, 7:10]),
        yvec=np.ascontiguousarray(floats[:, 10:13]),
        length=np.ascontiguousarray(floats[:, 13]),
        total_beams=int(n_beams),
        skip_reasons={},
    )

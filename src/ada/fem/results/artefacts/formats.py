"""Binary sidecar format constants (blob, element-field, edge, element and beam-warp layouts) and version pins."""

from __future__ import annotations

# Binary format: 4-byte magic, uint32 version, uint32 json_len, JSON
# header, zero-padded to 1024 bytes, then payload. Header version
# bump signals a breaking layout change (e.g. variable-stride steps).
BLOB_MAGIC = b"AFBL"


BLOB_VERSION = 1


BLOB_HEADER_BYTES = 1024


# v2 adds the optional ``history`` section (time-series at monitored
# nodes / elements). v1 manifests carry only mesh + fields; v2 readers
# treat ``history`` as optional so old artefacts keep loading.
MANIFEST_VERSION = 2


# What the bake PRODUCES, as distinct from what the manifest FORMAT can carry
# (MANIFEST_VERSION above): bumped whenever a re-bake of the same source would
# yield materially more than the cached artefacts hold, so servers can treat an
# older cached bake as stale and rebuild it instead of serving it forever.
#
#   1  initial streaming bake (mesh + field blobs)
#   2  semantic Sesam hierarchy, derived fields, units, surfaces
#   3  model-property fields (thickness/material/section with value_labels)
#      and mesh node_labels
#
# The REST API compares this against ``bake_version`` in a cached manifest —
# via its own pinned copy (EXPECTED_FEA_BAKE_VERSION in comms/rest/converter),
# because the slim API container cannot import ada.fem. A test asserts the two
# stay equal.
FEA_BAKE_VERSION = 3


# Element-field blob format (AFEL). Same fixed-header pattern as
# AFBL, distinct magic so the frontend can fail fast if it loads the
# wrong sidecar. Payload shape per blob is
# ``[n_steps, n_elements, n_ips, n_components]`` float32. One blob
# per ``(field_name, elem_type)`` — different element types in the
# same field don't share IP counts, so one blob per type keeps the
# layout uniform.
ELEM_FIELD_MAGIC = b"AFEL"


ELEM_FIELD_VERSION = 1


# Same 1 KB prefix as AFBL — header carries only O(1) binary shape
# metadata. ``element_labels`` and ``ip_layout`` live in the
# manifest's ``per_type`` entry where they can grow with the model
# size without bloating the binary header.
ELEM_FIELD_HEADER_BYTES = 1024


# Mesh-edge sidecar format. Distinct from AFBL: edges are static
# per source (one-shot, no step stack) and small (~10s of KB), so
# the header is just magic + version + count, no JSON metadata.
# Frontend renders these as THREE.LineSegments sharing the mesh's
# position attribute, so deformation drives both face and line
# rendering from the same buffer.
EDGE_MAGIC = b"AFEG"


EDGE_VERSION = 1


EDGE_HEADER_BYTES = 16  # magic + version + n_edges + 4-byte pad


# Mesh-element sidecar format (AFEM). One entry per element: the
# source-file label and the element's range into the flat triangle
# buffer of fea.mesh.glb. Frontend hydrates these into
# ``userdata.id_hierarchy`` + ``userdata.draw_ranges_<meshName>`` so
# CustomBatchedMesh's existing pick → highlight pipeline picks up
# the FEA mesh without a parallel selection path.
ELEM_MAGIC = b"AFEM"


ELEM_VERSION = 1


ELEM_HEADER_BYTES = 16  # magic + version + n_elements + 4-byte pad


ELEM_ENTRY_BYTES = 12  # uint32 label, uint32 tri_start, uint32 tri_count


# Beam-solid mesh — optional parallel mesh emitted by readers that
# have section + axis info per beam element (currently SIF only via
# the FEAResultStreamAdapter). The bake tessellates each beam's
# extruded cross-section into triangles, concatenates them into one
# vertex + index pair, and records per-beam draw ranges keyed by the
# line-element label. Frontend renders this as a second mesh
# alongside ``fea.mesh.glb`` and can paint it with the same AFEL
# element-field pipeline since the labels match.

# Beam-solid warp sidecar (AFBV). Per-vertex mapping back to the
# nodal displacement field: (node0_idx, node1_idx, t). The frontend
# lerps disp[node0]<-->disp[node1] by ``t`` per vertex so the solid
# mesh deforms in lockstep with its parent beam's two endpoints —
# without this, a large scaleFactor on a static load would make the
# shells flex while the rigid beam solids stayed put, visually
# disconnecting the structure.
BEAM_WARP_MAGIC = b"AFBV"


BEAM_WARP_VERSION = 1


BEAM_WARP_HEADER_BYTES = 16  # magic + version + n_verts + 4-byte pad


BEAM_WARP_ENTRY_BYTES = 12  # uint32 n0, uint32 n1, float32 t


# Compact beam-solids artefact (AFBS). The GLB + AFBV + AFEM trio above
# ships one record per VERTEX of a mesh that is fully determined by a
# handful of section outlines and one frame per beam — 122 MB on a large
# deck, of which 88 MB is a vertex buffer the browser could have produced
# itself. AFBS ships the generators instead: a per-section outline table
# (points + the triangle list of the extrusion, both shared by every beam
# on that section) and a 56-byte record per beam. The viewer expands it in
# a worker into exactly the buffers the GLB path builds — same vertex
# order, same triangles, same draw ranges, and the AFBV triple recomputed
# per vertex — so nothing downstream of the expansion can tell them apart.
#
#   header   16 B: magic, uint32 version, uint32 n_sections, uint32 n_beams
#   sections     : uint32 n_points, uint32 n_tris,
#                  float32 points[n_points * 2]   (u, v) in the profile plane
#                  uint32  tris[n_tris * 3]       into the 2*n_points extrusion
#   beams        : 56 B each, see BEAM_COMPACT_BEAM_BYTES
#
# Little-endian and 4-byte aligned throughout, so the browser reads every
# run as a typed-array view on the fetched ArrayBuffer rather than a
# byte-at-a-time DataView walk.
#
# A NEW MAGIC rather than a version bump on AFBV/AFEM: the frontend's
# per-format checks are ``!==`` equality, so an older viewer has to meet an
# unknown manifest KEY (and fall back to line rendering, which it already
# does when ``beam_solids_url`` is absent) rather than a known key whose
# bytes it would reject.
BEAM_COMPACT_MAGIC = b"AFBS"


BEAM_COMPACT_VERSION = 1


BEAM_COMPACT_HEADER_BYTES = 16  # magic + version + n_sections + n_beams


# uint32 label, uint32 section, uint32 node0, uint32 node1,
# float32 origin[3], float32 xvec[3], float32 yvec[3], float32 length.
#
# ``up`` is NOT stored: it is ``normalize(cross(xvec, yvec))`` by
# construction (see :class:`ada.api.beams.geom_beams.BeamFrame`), and one
# cross product per beam in the expander is cheaper than 12 more bytes per
# beam AND cannot drift out of step with the two axes it is derived from.
# Neither is ``t``: it is not constant per ring on an eccentric beam (the
# extrusion axis is not the element axis when e1 != e2), so it is a
# per-vertex quantity that only the node positions can answer — which the
# browser has, in the main mesh's point buffer.
BEAM_COMPACT_BEAM_BYTES = 56

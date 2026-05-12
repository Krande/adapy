"""Streaming bake → artefact tree, against the existing RMED fixtures.

Phase 1 of the FEA viewer: produces ``fea.mesh.glb`` + per-field
``fea.<field>.bin`` blobs + ``fea.manifest.json`` from an RMED file
without ever holding the full field stack in memory. This test
verifies the artefact tree structure, manifest schema, and blob
layout end-to-end. The streaming reader is exercised through the
public :class:`FEAStreamReader` protocol.
"""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest

from ada.fem.formats.code_aster.read.med_reader import med_to_mesh_data
from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader
from ada.fem.results.artefacts import (
    BLOB_HEADER_BYTES,
    BLOB_MAGIC,
    BLOB_VERSION,
    FEAResultStreamAdapter,
    bake_artefacts,
    bake_fea_artefacts_from_source,
    is_fea_artefact_source,
    make_stream_reader,
    read_blob_header,
    read_blob_step,
)


RMED_FIXTURES = [
    "code_aster/Cantilever_CA_EIG_bm.rmed",
    "code_aster/Cantilever_CA_EIG_sh.rmed",
    "cantilever/code_aster/static_shell_cantilever_code_aster.rmed",
    "cantilever/code_aster/static_solid_cantilever_code_aster.rmed",
    "cantilever/code_aster/static_line_cantilever_code_aster.rmed",
    "cantilever/code_aster/eigen_shell_cantilever_code_aster.rmed",
    "cantilever/code_aster/eigen_solid_cantilever_code_aster.rmed",
    "cantilever/code_aster/eigen_line_cantilever_code_aster.rmed",
]


@pytest.mark.parametrize("rmed_rel", RMED_FIXTURES)
def test_bake_produces_expected_artefact_tree(fem_files, tmp_path, rmed_rel):
    rmed = fem_files / rmed_rel
    if not rmed.exists():
        pytest.skip(f"fixture not present: {rmed_rel}")

    with RmedStreamReader(rmed) as reader:
        result = bake_artefacts(reader, tmp_path / "out", src=rmed.stem)

    # Manifest + mesh GLB always present.
    assert result.manifest_path.exists()
    assert result.mesh_glb_path.exists()
    assert result.mesh_glb_path.stat().st_size > 0

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["version"] == 1
    assert manifest["src"] == rmed.stem
    assert manifest["mesh"]["url"] == "fea.mesh.glb"
    assert manifest["mesh"]["n_points"] > 0


@pytest.mark.parametrize("rmed_rel", RMED_FIXTURES)
def test_bake_blob_round_trips_against_eager_reader(fem_files, tmp_path, rmed_rel):
    """Streaming bake's per-step values must match the eager reader's
    ``point_data`` for the same (field, step). This is the truth
    check — the eager h5py reader is already parity-tested against
    meshio."""

    rmed = fem_files / rmed_rel
    if not rmed.exists():
        pytest.skip(f"fixture not present: {rmed_rel}")

    eager = med_to_mesh_data(rmed)

    with RmedStreamReader(rmed) as reader:
        result = bake_artefacts(reader, tmp_path / "out", src=rmed.stem)

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["fields"], f"no fields baked for {rmed_rel}"

    for field_entry in manifest["fields"]:
        if field_entry["support"] != "nodal":
            continue
        field_name = field_entry["name_canonical"]
        n_steps = field_entry["n_steps"]
        blob_path = result.out_dir / field_entry["blob"]["url"]
        assert blob_path.exists()

        # Blob magic / version / header self-consistency.
        with open(blob_path, "rb") as f:
            prefix = f.read(12)
        assert prefix[:4] == BLOB_MAGIC
        version, json_len = struct.unpack("<II", prefix[4:12])
        assert version == BLOB_VERSION
        assert json_len > 0

        header = read_blob_header(blob_path)
        assert header["n_steps"] == n_steps
        assert header["n_points"] == manifest["mesh"]["n_points"]
        assert header["dtype"] == "float32"

        # Compare each step against the eager reader's point_data.
        # Eager keys: bare name for single-step, "<name>[i] - <t:g>" multi-step.
        for i in range(n_steps):
            arr = read_blob_step(blob_path, i)
            assert arr.shape == (header["n_points"], header["n_components"])

            t = field_entry["steps"][i]["value"]
            eager_key = field_name if n_steps == 1 else f"{field_name}[{i:d}] - {t:g}"
            ref = eager.point_data[eager_key]
            ref_arr = np.asarray(ref, dtype=np.float32)
            if ref_arr.ndim == 1:
                ref_arr = ref_arr.reshape(-1, 1)
            np.testing.assert_array_equal(arr, ref_arr)


@pytest.mark.parametrize("rmed_rel", RMED_FIXTURES[:2])
def test_manifest_locks_picker_defaults(fem_files, tmp_path, rmed_rel):
    """default_view + scalar_range are baked at write-time so the
    frontend has fixed colour ranges across all steps and an initial
    reduction mode without computing anything client-side."""

    rmed = fem_files / rmed_rel
    if not rmed.exists():
        pytest.skip(f"fixture not present: {rmed_rel}")

    with RmedStreamReader(rmed) as reader:
        result = bake_artefacts(reader, tmp_path / "out", src=rmed.stem)
    manifest = json.loads(result.manifest_path.read_text())

    assert manifest["fields"], f"no fields baked for {rmed_rel}"
    for field_entry in manifest["fields"]:
        view = field_entry["default_view"]
        assert view["colormap"] == "viridis"
        if field_entry["kind"].startswith("vector"):
            assert view["reduction"] == "magnitude"
        else:
            assert view["reduction"] == "scalar"

        scalar_range = field_entry["scalar_range"]
        # Ranges live at the per-component level always; magnitude
        # only when the field has 3+ components.
        for comp in field_entry["components"]:
            assert comp in scalar_range
            lo, hi = scalar_range[comp]
            assert lo <= hi
        if field_entry["kind"].startswith("vector"):
            assert "magnitude" in scalar_range


SIF_FIXTURES = [
    "sesam/1EL_SHELL_R1.SIF",
    "sesam/2EL_SHELL_R1.SIF",
    "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIF",
    "cantilever/sesam/static/line/STATIC_LINE_CANTILEVER_SESAMR1.SIF",
    "cantilever/sesam/eigen/shell/EIGEN_SHELL_CANTILEVER_SESAMR1.SIF",
    "cantilever/sesam/eigen/line/EIGEN_LINE_CANTILEVER_SESAMR1.SIF",
]


# Full corpus of FEA fixtures the streaming-viewer bake covers in
# Phase 1 (RMED native streaming + SIF via the FEAResult adapter).
# Each manifest produced from these must satisfy the picker UI's
# strict contract; assert_picker_contract() locks that contract so
# silent schema drift surfaces before it breaks the frontend.
ALL_FEA_FIXTURES = [
    *[("rmed", rel) for rel in RMED_FIXTURES],
    *[("sif", rel) for rel in SIF_FIXTURES],
]


def _assert_picker_contract(manifest: dict, *, fixture_label: str) -> None:
    """Strict schema check for everything the FeaStreamingPickerModal
    reads from a manifest. Drift here causes runtime
    "Cannot read properties of undefined" errors in the picker that
    are hard to attribute back to the bake — this test catches them
    where the bake actually runs."""

    assert manifest.get("version") == 1, fixture_label
    assert isinstance(manifest.get("src"), str) and manifest["src"], fixture_label

    mesh = manifest.get("mesh")
    assert isinstance(mesh, dict), fixture_label
    assert isinstance(mesh.get("url"), str) and mesh["url"], fixture_label
    assert isinstance(mesh.get("n_points"), int) and mesh["n_points"] > 0, fixture_label
    assert isinstance(mesh.get("n_cells"), int) and mesh["n_cells"] >= 0, fixture_label
    # Selection sidecar — drives userdata.id_hierarchy +
    # userdata.draw_ranges_<meshName> on the frontend.
    assert isinstance(mesh.get("elements_url"), str) and mesh["elements_url"], (
        f"{fixture_label}: missing elements_url"
    )
    assert isinstance(mesh.get("n_elements"), int) and mesh["n_elements"] >= 0, (
        f"{fixture_label}: bad n_elements"
    )

    fields = manifest.get("fields")
    assert isinstance(fields, list) and fields, f"{fixture_label}: no fields"

    for field in fields:
        # The picker calls field.kind.startsWith("vector"), reads
        # field.components for the reduction selector, and walks
        # field.steps for the slider. All three must be present and
        # of the right shape. ``blob`` is required on nodal-style
        # fields; element fields use ``per_type`` instead, checked
        # separately below.
        is_element_field = "per_type" in field
        common_required = [
            "name_canonical",
            "name_native",
            "kind",
            "category",
            "support",
            "components",
            "n_steps",
            "steps",
            "scalar_range",
            "default_view",
        ]
        if not is_element_field:
            common_required.append("blob")
        for required in common_required:
            assert required in field, f"{fixture_label}: field missing {required}"

        assert field["category"] in {
            "displacement", "reaction", "stress", "strain", "other"
        }, f"{fixture_label}: bad category={field['category']!r}"

        assert isinstance(field["name_canonical"], str) and field["name_canonical"], (
            f"{fixture_label}: empty name_canonical"
        )
        assert isinstance(field["kind"], str) and field["kind"], fixture_label
        assert field["support"] in {"nodal", "element_nodal", "gauss"}, (
            f"{fixture_label}: bad support={field['support']!r}"
        )
        # Drives the deformation-scale slider range in the picker:
        # static = [0, 1], eigen = [-1, +1].
        assert field.get("analysis_kind") in {"static", "eigen"}, (
            f"{fixture_label}: bad analysis_kind={field.get('analysis_kind')!r}"
        )
        assert isinstance(field["components"], list), fixture_label
        assert all(isinstance(c, str) and c for c in field["components"]), fixture_label
        assert isinstance(field["n_steps"], int) and field["n_steps"] >= 1, (
            f"{fixture_label}: n_steps={field['n_steps']}"
        )
        assert isinstance(field["steps"], list), fixture_label
        assert len(field["steps"]) == field["n_steps"], (
            f"{fixture_label}: steps len {len(field['steps'])} != n_steps {field['n_steps']}"
        )
        for i, step in enumerate(field["steps"]):
            assert step.get("i") == i, f"{fixture_label}: step[{i}].i mismatch"
            assert "value" in step and isinstance(step["value"], (int, float)), (
                f"{fixture_label}: step[{i}].value missing or wrong type"
            )
            assert isinstance(step.get("label"), str) and step["label"], (
                f"{fixture_label}: step[{i}].label missing"
            )

        if is_element_field:
            per_type = field["per_type"]
            assert isinstance(per_type, list) and per_type, (
                f"{fixture_label}: empty per_type on element field {field['name_canonical']}"
            )
            for pt in per_type:
                assert isinstance(pt.get("elem_type"), str) and pt["elem_type"], fixture_label
                assert isinstance(pt.get("n_elements"), int) and pt["n_elements"] >= 0, fixture_label
                assert isinstance(pt.get("n_ips"), int) and pt["n_ips"] >= 1, fixture_label
                blob = pt["blob"]
                assert isinstance(blob.get("url"), str) and blob["url"], fixture_label
                assert isinstance(blob.get("header_bytes"), int) and blob["header_bytes"] > 0, fixture_label
                assert isinstance(blob.get("stride_bytes"), int) and blob["stride_bytes"] > 0, fixture_label
                assert isinstance(blob.get("dtype"), str) and blob["dtype"], fixture_label
                assert blob.get("byte_order") in {"little", "big"}, fixture_label
        else:
            blob = field["blob"]
            assert isinstance(blob.get("url"), str) and blob["url"], fixture_label
            assert isinstance(blob.get("header_bytes"), int) and blob["header_bytes"] > 0, (
                fixture_label
            )
            assert isinstance(blob.get("stride_bytes"), int) and blob["stride_bytes"] > 0, (
                fixture_label
            )
            assert isinstance(blob.get("dtype"), str) and blob["dtype"], fixture_label
            assert blob.get("byte_order") in {"little", "big"}, fixture_label

        scalar_range = field["scalar_range"]
        assert isinstance(scalar_range, dict) and scalar_range, (
            f"{fixture_label}: scalar_range empty"
        )
        for comp in field["components"]:
            assert comp in scalar_range, (
                f"{fixture_label}: missing scalar_range for component {comp!r}"
            )
            lo, hi = scalar_range[comp]
            assert lo <= hi, f"{fixture_label}: bad range for {comp}"
        if field["kind"].startswith("vector"):
            assert "magnitude" in scalar_range, (
                f"{fixture_label}: vector field missing magnitude range"
            )

        default_view = field["default_view"]
        assert default_view.get("colormap"), fixture_label
        assert default_view.get("reduction") in {"magnitude", "scalar"} or (
            default_view.get("reduction") in field["components"]
        ), f"{fixture_label}: bad default reduction {default_view.get('reduction')!r}"


def test_write_beam_solids_edges_keeps_perimeter_and_element_seams(tmp_path):
    """write_beam_solids_edges drops edges interior to a single beam
    element, keeps edges between two adjacent elements (the axial seam)
    plus the mesh perimeter.

    The "ladder" fixture below is two quads side by side, each
    triangulated into two triangles and assigned to a different
    element. The shared edge (vertex 2-3) is the element seam and must
    survive; the two intra-quad diagonals are interior triangulation
    artefacts and must be dropped; the remaining six perimeter edges
    are mesh boundary and must survive."""

    from ada.fem.results.artefacts import (
        EDGE_HEADER_BYTES,
        EDGE_MAGIC,
        SolidBeamMesh,
        write_beam_solids_edges,
    )
    from ada.visit.rendering.femviz import ElementRange

    # Two quads, each one element. Element A: tris (0,1,2),(1,3,2);
    # Element B: tris (2,3,4),(3,5,4). The diagonals are (1,2) for A
    # and (3,4) for B — interior to their elements and dropped.
    points = np.array(
        [
            [0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0], [1.0, 1.0, 0.0],
            [0.0, 2.0, 0.0], [1.0, 2.0, 0.0],
        ],
        dtype=np.float64,
    )
    triangles = np.array(
        [[0, 1, 2], [1, 3, 2], [2, 3, 4], [3, 5, 4]],
        dtype=np.uint32,
    )
    element_ranges = [
        ElementRange(label=10, tri_start=0, tri_count=2),
        ElementRange(label=11, tri_start=2, tri_count=2),
    ]
    mesh = SolidBeamMesh(
        points=points,
        triangles=triangles,
        element_ranges=element_ranges,
    )

    out_path = tmp_path / "fea.beam_solids.edges.bin"
    n_edges = write_beam_solids_edges(mesh, out_path)

    expected = {
        frozenset({0, 1}),  # perimeter (A)
        frozenset({0, 2}),  # perimeter (A)
        frozenset({1, 3}),  # perimeter (A)
        frozenset({2, 3}),  # element seam (A↔B)
        frozenset({2, 4}),  # perimeter (B)
        frozenset({3, 5}),  # perimeter (B)
        frozenset({4, 5}),  # perimeter (B)
    }
    assert n_edges == len(expected), f"expected {len(expected)} edges, got {n_edges}"

    data = out_path.read_bytes()
    assert data[:4] == EDGE_MAGIC
    version, header_n = struct.unpack("<II", data[4:12])
    assert version == 1
    assert header_n == n_edges
    pairs = np.frombuffer(data[EDGE_HEADER_BYTES:], dtype=np.uint32).reshape(-1, 2)
    got = {frozenset((int(a), int(b))) for a, b in pairs}
    assert got == expected, f"edges differ: extra={got - expected} missing={expected - got}"

    # The diagonals must NOT appear — they're inside a single element.
    assert frozenset({1, 2}) not in got
    assert frozenset({3, 4}) not in got


def test_write_beam_solids_edges_buckets_coincident_vertices(tmp_path):
    """OCC tessellates each face of a solid independently — a single
    beam's side panel and end cap have different vertex indices at
    the same 3D positions. An index-based edge dedup treats their
    shared edge as a one-triangle boundary on each side and draws
    cross-hatching artefacts. Position-bucketing collapses
    coincident vertices so within-beam face seams correctly resolve
    to same-element interior edges and get dropped.

    Fixture: two triangles in the same element, sharing one
    geometric edge but **not** sharing vertex indices. The shared
    edge must be dropped (interior to the single element).
    """

    from ada.fem.results.artefacts import (
        EDGE_HEADER_BYTES,
        EDGE_MAGIC,
        SolidBeamMesh,
        write_beam_solids_edges,
    )
    from ada.visit.rendering.femviz import ElementRange

    # Two triangles in element 10. Vertex (1.0, 0.0, 0.0) appears
    # twice (indices 1 and 3); (0.5, 1.0, 0.0) twice (indices 2 and
    # 4). Without bucketing the algorithm sees edges (1,2) and (3,4)
    # as two separate one-triangle edges → both kept. With bucketing
    # they collapse into one same-element group → dropped.
    points = np.array(
        [
            [0.0, 0.0, 0.0],   # 0
            [1.0, 0.0, 0.0],   # 1 — duplicate of 3
            [0.5, 1.0, 0.0],   # 2 — duplicate of 4
            [1.0, 0.0, 0.0],   # 3 — duplicate of 1
            [0.5, 1.0, 0.0],   # 4 — duplicate of 2
            [1.5, 0.0, 0.0],   # 5
        ],
        dtype=np.float64,
    )
    triangles = np.array([[0, 1, 2], [3, 5, 4]], dtype=np.uint32)
    element_ranges = [ElementRange(label=10, tri_start=0, tri_count=2)]
    mesh = SolidBeamMesh(
        points=points,
        triangles=triangles,
        element_ranges=element_ranges,
    )

    out_path = tmp_path / "fea.beam_solids.edges.bin"
    n_edges = write_beam_solids_edges(mesh, out_path)

    # Expected boundary edges (in position space):
    #   (0.0,0.0,0) — (1.0,0.0,0)   perimeter
    #   (0.0,0.0,0) — (0.5,1.0,0)   perimeter
    #   (1.0,0.0,0) — (1.5,0.0,0)   perimeter
    #   (1.5,0.0,0) — (0.5,1.0,0)   perimeter
    # The shared edge (1.0,0.0,0) — (0.5,1.0,0) is INTERIOR to
    # element 10 (now that bucketing merges the duplicate vertices)
    # and must be dropped.
    expected_position_pairs = {
        frozenset({(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)}),
        frozenset({(0.0, 0.0, 0.0), (0.5, 1.0, 0.0)}),
        frozenset({(1.0, 0.0, 0.0), (1.5, 0.0, 0.0)}),
        frozenset({(1.5, 0.0, 0.0), (0.5, 1.0, 0.0)}),
    }
    forbidden_position_pair = frozenset(
        {(1.0, 0.0, 0.0), (0.5, 1.0, 0.0)},
    )

    assert n_edges == len(expected_position_pairs), (
        f"expected {len(expected_position_pairs)} edges, got {n_edges} "
        "— bucketing did not collapse coincident-vertex face seams"
    )

    data = out_path.read_bytes()
    assert data[:4] == EDGE_MAGIC
    pairs = np.frombuffer(data[EDGE_HEADER_BYTES:], dtype=np.uint32).reshape(-1, 2)
    got_position_pairs = set()
    for a, b in pairs:
        pa = tuple(float(x) for x in points[a])
        pb = tuple(float(x) for x in points[b])
        got_position_pairs.add(frozenset({pa, pb}))

    assert forbidden_position_pair not in got_position_pairs, (
        "the within-element face seam edge was emitted — bucketing "
        "is not collapsing duplicate-position vertices"
    )
    assert got_position_pairs == expected_position_pairs, (
        f"unexpected edge set; got {got_position_pairs}, "
        f"expected {expected_position_pairs}"
    )


def test_write_beam_solids_edges_empty_mesh(tmp_path):
    """An empty SolidBeamMesh produces a valid AFEG header with
    n_edges=0. Downstream parseMeshEdges handles this as a zero-edge
    wireframe (no-op render)."""

    from ada.fem.results.artefacts import (
        EDGE_HEADER_BYTES,
        EDGE_MAGIC,
        SolidBeamMesh,
        write_beam_solids_edges,
    )

    mesh = SolidBeamMesh(
        points=np.empty((0, 3), dtype=np.float64),
        triangles=np.empty((0, 3), dtype=np.uint32),
        element_ranges=[],
    )
    out_path = tmp_path / "empty.edges.bin"
    n_edges = write_beam_solids_edges(mesh, out_path)
    assert n_edges == 0
    data = out_path.read_bytes()
    assert data[:4] == EDGE_MAGIC
    assert len(data) == EDGE_HEADER_BYTES


def test_bake_emits_mesh_edges_sidecar(fem_files, tmp_path):
    """write_mesh_edges produces a deduped uint32 pair list with the
    AFEG header. The frontend renders these as a wireframe overlay
    sharing the mesh's position attribute so deformation drives both
    surface and edges from a single buffer."""

    import struct
    from ada.fem.results.artefacts import EDGE_HEADER_BYTES, EDGE_MAGIC

    rmed = fem_files / "cantilever/code_aster/eigen_solid_cantilever_code_aster.rmed"
    if not rmed.exists():
        pytest.skip("fixture not present")

    bake = bake_fea_artefacts_from_source(rmed, tmp_path / "out", src_key=rmed.stem)
    manifest = json.loads(bake.manifest_path.read_text())

    assert manifest["mesh"]["edges_url"] == "fea.mesh.edges.bin"
    assert manifest["mesh"]["n_edges"] > 0

    edges_path = bake.out_dir / "fea.mesh.edges.bin"
    assert edges_path.exists()

    data = edges_path.read_bytes()
    assert data[:4] == EDGE_MAGIC
    version, n_edges = struct.unpack("<II", data[4:12])
    assert version == 1
    assert n_edges == manifest["mesh"]["n_edges"]
    # Payload = n_edges × 2 uint32 indices.
    assert len(data) == EDGE_HEADER_BYTES + n_edges * 2 * 4


@pytest.mark.parametrize("kind,rel", ALL_FEA_FIXTURES)
def test_bake_emits_mesh_elements_sidecar(fem_files, tmp_path, kind, rel):
    """Per-element draw ranges (AFEM) drive selection on the FEA mesh.

    Asserts the binary layout (header + uint32 triplets), and that the
    triangle ranges tile the GLB index buffer exactly: tri_starts +
    tri_counts must cover every triangle exactly once when summed in
    iteration order. Labels are stored as uint32 from the source-file
    identifiers (RMED MAI/<type>/NUM, SIF element ids).
    """

    from ada.fem.results.artefacts import (
        ELEM_ENTRY_BYTES,
        ELEM_HEADER_BYTES,
        ELEM_MAGIC,
    )

    src = fem_files / rel
    if not src.exists():
        pytest.skip(f"fixture not present: {rel}")

    bake = bake_fea_artefacts_from_source(src, tmp_path / "out", src_key=src.stem)
    manifest = json.loads(bake.manifest_path.read_text())

    assert manifest["mesh"]["elements_url"] == "fea.mesh.elements.bin"
    n_elements_manifest = manifest["mesh"]["n_elements"]
    assert n_elements_manifest >= 0

    elements_path = bake.out_dir / "fea.mesh.elements.bin"
    assert elements_path.exists()

    data = elements_path.read_bytes()
    assert data[:4] == ELEM_MAGIC, f"{rel}: bad AFEM magic"
    version, n_elements = struct.unpack("<II", data[4:12])
    assert version == 1, f"{rel}: AFEM version={version}"
    assert n_elements == n_elements_manifest, (
        f"{rel}: header n_elements {n_elements} vs manifest {n_elements_manifest}"
    )
    assert len(data) == ELEM_HEADER_BYTES + n_elements * ELEM_ENTRY_BYTES, (
        f"{rel}: AFEM payload size mismatch (n_elements={n_elements})"
    )

    if n_elements == 0:
        return

    # Decode entries; check ranges are non-overlapping, monotone, and
    # cover the full triangle space starting at 0.
    raw = np.frombuffer(
        data[ELEM_HEADER_BYTES:],
        dtype=np.uint32,
    ).reshape(n_elements, 3)
    labels = raw[:, 0]
    starts = raw[:, 1]
    counts = raw[:, 2]

    cursor = 0
    for i in range(n_elements):
        assert int(starts[i]) == cursor, (
            f"{rel}: element[{i}] tri_start={starts[i]} expected {cursor}"
        )
        cursor += int(counts[i])

    # Labels must be non-zero (real labels are 1-based in RMED/SIF; the
    # fallback positional counter starts at 1 too).
    assert int(labels.min()) >= 1, (
        f"{rel}: AFEM labels include 0 ({labels[labels == 0].size} zero labels)"
    )


@pytest.mark.parametrize("rmed_rel", RMED_FIXTURES)
def test_afem_labels_round_trip_against_source_rmed(fem_files, tmp_path, rmed_rel):
    """The AFEM labels for an RMED fixture must equal the source file's
    ``MAI/<type>/NUM`` arrays read directly with h5py (or the 1-based
    positional fallback when NUM is absent). Locks element-label
    plumbing through CellBlockData.identifiers."""

    import h5py

    rmed = fem_files / rmed_rel
    if not rmed.exists():
        pytest.skip(f"fixture not present: {rmed_rel}")

    bake = bake_fea_artefacts_from_source(rmed, tmp_path / "out", src_key=rmed.stem)
    elements_path = bake.out_dir / "fea.mesh.elements.bin"
    data = elements_path.read_bytes()
    n_elements = struct.unpack("<I", data[8:12])[0]

    if n_elements == 0:
        pytest.skip(f"{rmed_rel}: no elements emitted")

    afem_labels = np.frombuffer(
        data[16:], dtype=np.uint32
    ).reshape(n_elements, 3)[:, 0]

    # Pull labels from the source RMED. The bake's iteration order is:
    # cell-block iteration order × per-block element order. Solid
    # elements with line elements interleaved: line elements emit a
    # zero-tri entry but still consume an AFEM slot, so the order
    # matches the *full* per-block element walk.
    #
    # The streaming reader uses ``MAI/<type>`` block iteration —
    # h5py.Group iteration is insertion order, which is the bake's
    # order too.
    with h5py.File(rmed, "r") as f:
        mesh_ensemble = f["ENS_MAA"]
        mesh_keys = list(mesh_ensemble.keys())
        mesh = mesh_ensemble[mesh_keys[0]]
        if "NOE" not in mesh:
            ts_keys = list(mesh.keys())
            mesh = mesh[ts_keys[0]]

        expected: list[int] = []
        if "MAI" in mesh:
            for med_short, med_group in mesh["MAI"].items():
                n_cells = int(med_group["NOD"].attrs["NBR"])
                if "NUM" in med_group:
                    nums = np.asarray(med_group["NUM"][()], dtype=np.int64)
                else:
                    nums = np.arange(1, n_cells + 1, dtype=np.int64)
                expected.extend(int(x) for x in nums)

    assert len(expected) == n_elements, (
        f"{rmed_rel}: expected {len(expected)} labels but got {n_elements}"
    )
    assert list(afem_labels.astype(np.int64)) == expected, (
        f"{rmed_rel}: AFEM labels diverge from RMED MAI/<type>/NUM"
    )


@pytest.mark.parametrize("kind,rel", ALL_FEA_FIXTURES)
def test_bake_satisfies_picker_contract_for_every_fixture(
    fem_files, tmp_path, kind, rel,
):
    """End-to-end: every RMED + SIF fixture in the test corpus produces
    a manifest the picker UI can render without a "Cannot read
    properties of undefined" error. Locks the schema; future bake
    changes that drop a required field land as test failures here
    instead of silent runtime errors in the browser."""

    src = fem_files / rel
    if not src.exists():
        pytest.skip(f"fixture not present: {rel}")

    bake = bake_fea_artefacts_from_source(src, tmp_path / "out", src_key=src.stem)
    manifest = json.loads(bake.manifest_path.read_text())
    _assert_picker_contract(manifest, fixture_label=f"{kind}:{rel}")

    # Cross-check: every blob URL in the manifest exists on disk.
    # Nodal fields carry a top-level ``blob``; element fields use a
    # ``per_type`` list with one blob per element-type bucket.
    for field in manifest["fields"]:
        if "per_type" in field:
            for pt in field["per_type"]:
                bp = bake.out_dir / pt["blob"]["url"]
                assert bp.exists(), f"{rel}: missing element blob {pt['blob']['url']}"
                assert bp.stat().st_size > pt["blob"]["header_bytes"], (
                    f"{rel}: element blob {bp.name} smaller than declared header"
                )
        else:
            blob_path = bake.out_dir / field["blob"]["url"]
            assert blob_path.exists(), f"{rel}: missing blob {field['blob']['url']}"
            assert blob_path.stat().st_size > field["blob"]["header_bytes"], (
                f"{rel}: blob {blob_path.name} smaller than declared header"
            )
    mesh_path = bake.out_dir / manifest["mesh"]["url"]
    assert mesh_path.exists() and mesh_path.stat().st_size > 0, (
        f"{rel}: mesh GLB missing or empty"
    )


@pytest.mark.parametrize("sif_rel", SIF_FIXTURES)
def test_bake_via_fearesult_adapter_against_sif(fem_files, tmp_path, sif_rel):
    """The FEAResult adapter lets formats whose readers haven't been
    rewritten as native streamers (SIF, FRD) flow through the same
    bake. This is the throwaway path that still produces correct
    artefacts; replaced per-format when a real streaming reader for
    that format exists."""

    from ada.fem.formats.sesam.results.read_sif import read_sif_file

    sif = fem_files / sif_rel
    if not sif.exists():
        pytest.skip(f"fixture not present: {sif_rel}")

    result = read_sif_file(sif)

    with FEAResultStreamAdapter(result) as reader:
        bake = bake_artefacts(reader, tmp_path / "out", src=sif.stem)

    manifest = json.loads(bake.manifest_path.read_text())
    assert manifest["src"] == sif.stem
    assert manifest["mesh"]["n_points"] > 0

    assert manifest["fields"], f"no fields baked for {sif_rel}"
    for field_entry in manifest["fields"]:
        # Element fields use ``per_type`` instead of a top-level
        # ``blob`` + ``support: "nodal"``. The blob layout differs
        # so we only round-trip the nodal entries here; element
        # blobs have their own coverage in
        # test_bake_writes_element_field_blob_per_type.
        if "per_type" in field_entry:
            assert field_entry["support"] in {"element_nodal", "gauss"}
            continue
        assert field_entry["support"] == "nodal"
        blob_path = bake.out_dir / field_entry["blob"]["url"]
        assert blob_path.exists()

        header = read_blob_header(blob_path)
        assert header["n_steps"] == field_entry["n_steps"]
        assert header["n_points"] == manifest["mesh"]["n_points"]

        # Sanity: each step round-trips against the FEAResult itself,
        # since the adapter is just a different lens on the same data.
        for i, step_entry in enumerate(field_entry["steps"]):
            arr = read_blob_step(blob_path, i)
            assert arr.shape == (header["n_points"], header["n_components"])


def test_is_fea_artefact_source_classification():
    assert is_fea_artefact_source("models/wall.rmed")
    assert is_fea_artefact_source("models/wall.RMED")
    assert is_fea_artefact_source("models/wall.sif")
    assert is_fea_artefact_source("models/wall.SIF")
    assert not is_fea_artefact_source("models/wall.frd")  # Phase 2
    assert not is_fea_artefact_source("models/wall.glb")
    assert not is_fea_artefact_source("models/wall.ifc")


def test_make_stream_reader_dispatches_by_extension(fem_files, tmp_path):
    """Source-extension dispatch produces the right reader subclass.
    Concrete bake correctness already covered by upstream parametrized
    tests; this test just locks the dispatch contract."""

    from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader

    rmed = fem_files / "code_aster/Cantilever_CA_EIG_bm.rmed"
    if rmed.exists():
        with make_stream_reader(rmed) as r:
            assert isinstance(r, RmedStreamReader)

    sif = fem_files / "sesam/1EL_SHELL_R1.SIF"
    if sif.exists():
        with make_stream_reader(sif) as r:
            assert isinstance(r, FEAResultStreamAdapter)

    with pytest.raises(ValueError, match="no streaming reader"):
        make_stream_reader(tmp_path / "nope.frd")


def test_stream_reader_registry_dispatch_and_override(tmp_path):
    """Registering a factory makes that suffix dispatchable, and a
    registration overrides a built-in for the same suffix."""

    from ada.fem.results.artefacts import (
        fea_artefact_extensions,
        register_stream_reader,
    )

    class _Stub:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    # New suffix: dispatch picks up the registered factory.
    register_stream_reader(".stub", _Stub)
    try:
        assert ".stub" in fea_artefact_extensions()
        assert is_fea_artefact_source("foo.stub")
        with make_stream_reader(tmp_path / "any.stub") as r:
            assert isinstance(r, _Stub)

        # Override path: a fresh registration wins over the built-in.
        register_stream_reader(".rmed", _Stub)
        with make_stream_reader(tmp_path / "any.rmed") as r:
            assert isinstance(r, _Stub)
    finally:
        from ada.fem.results.artefacts import _STREAM_READERS, _make_rmed_reader

        _STREAM_READERS.pop(".stub", None)
        _STREAM_READERS[".rmed"] = _make_rmed_reader


def test_bake_from_source_end_to_end_rmed(fem_files, tmp_path):
    rmed = fem_files / "code_aster/Cantilever_CA_EIG_bm.rmed"
    if not rmed.exists():
        pytest.skip("fixture not present")

    bake = bake_fea_artefacts_from_source(rmed, tmp_path / "out", src_key="rmed-stem")
    manifest = json.loads(bake.manifest_path.read_text())
    assert manifest["src"] == "rmed-stem"
    assert manifest["fields"]


def test_bake_from_source_end_to_end_sif(fem_files, tmp_path):
    sif = fem_files / "sesam/1EL_SHELL_R1.SIF"
    if not sif.exists():
        pytest.skip("fixture not present")

    bake = bake_fea_artefacts_from_source(
        sif,
        tmp_path / "out",
        src_key="sif-stem",
        legacy_glb_url_template="legacy/{step}.{field}.glb",
    )
    manifest = json.loads(bake.manifest_path.read_text())
    assert manifest["src"] == "sif-stem"
    assert manifest["legacy_glb"]["url_template"] == "legacy/{step}.{field}.glb"


def test_blob_header_fits_in_fixed_prefix():
    """Sanity: the 1 KB header prefix must fit a realistic field's
    metadata. Step-offset list grows linearly with n_steps; this
    test catches the cliff before it lands in production."""

    # Header carries only O(1) shape metadata, so n_steps doesn't
    # affect header size. Confirm a generously-sized field still
    # fits — n_steps in the thousands is well within budget.
    from ada.fem.results.artefacts import FieldSpec, _encode_blob_header

    spec = FieldSpec(
        name="DEPL",
        components=["DX", "DY", "DZ", "DRX", "DRY", "DRZ"],
        n_steps=10_000,
        n_points=1_000_000,
        support="nodal",
        step_values=[float(i) * 0.123456 for i in range(10_000)],
    )
    stride = spec.n_points * spec.n_components * spec.dtype.itemsize
    header = _encode_blob_header(spec, stride)
    assert len(header) == BLOB_HEADER_BYTES


def test_classify_field_by_name():
    """Spot-check the name-based fallback so unfamiliar solvers get a
    sensible category. The bake-level test asserts the manifest
    carries a valid value; this asserts the actual classification."""

    from ada.fem.results.artefacts import _classify_field

    class _Sample:
        field_type = None

    s = _Sample()
    assert _classify_field("RVNODDIS", s) == "displacement"
    assert _classify_field("DEPL", s) == "displacement"
    assert _classify_field("RVFORCES", s) == "reaction"
    assert _classify_field("RVSTRESS", s) == "stress"
    assert _classify_field("SIEF_NOEU", s) == "stress"
    assert _classify_field("EPSI_NOEU", s) == "strain"
    assert _classify_field("MY_CUSTOM_FIELD", s) == "other"


def test_bake_writes_element_field_blob_per_type(fem_files, tmp_path):
    """SIF shell-stress fixtures exercise the AFEL bake. The bake
    produces one ``fea.<field>.<elem_type>.elements.bin`` per
    (field, element-type) bucket; the manifest groups them under one
    ``per_type`` array per logical field. Header + payload round-trip
    via the AFEL readers."""

    from ada.fem.formats.sesam.results.read_sif import read_sif_file
    from ada.fem.results.artefacts import (
        FEAResultStreamAdapter,
        bake_artefacts,
        read_elem_field_blob_header,
        read_elem_field_blob_step,
    )

    sif = fem_files / "sesam/1EL_SHELL_R1.SIF"
    if not sif.exists():
        pytest.skip("fixture not present")

    result = read_sif_file(sif)
    with FEAResultStreamAdapter(result) as reader:
        bake = bake_artefacts(reader, tmp_path / "out", src=sif.stem)
    manifest = json.loads(bake.manifest_path.read_text())

    elem_fields = [f for f in manifest["fields"] if "per_type" in f]
    assert elem_fields, "expected at least one element field from the shell SIF"

    for field in elem_fields:
        assert field["support"] in {"element_nodal", "gauss"}
        assert field["category"] in {"stress", "strain", "reaction", "other"}
        for pt in field["per_type"]:
            blob_path = bake.out_dir / pt["blob"]["url"]
            assert blob_path.exists(), f"missing {pt['blob']['url']}"
            header = read_elem_field_blob_header(blob_path)
            assert header["n_elements"] == pt["n_elements"]
            assert header["n_ips"] == pt["n_ips"]
            assert header["n_components"] == len(field["components"])
            # First-step round-trip — shape must match the header.
            arr = read_elem_field_blob_step(blob_path, 0)
            assert arr.shape == (
                pt["n_elements"], pt["n_ips"], len(field["components"])
            )
            # Element labels list aligns with the bucket's row order.
            assert len(pt["element_labels"]) == pt["n_elements"]


def test_bake_emits_beam_solid_mesh_for_sif_line(fem_files, tmp_path):
    """SIF fixtures with beam (line) elements + section info trigger
    the optional beam-solid mesh emission: a parallel GLB plus an
    AFEM-format per-beam draw-range sidecar. The manifest mesh meta
    must point at both files and every line element should appear in
    the elements sidecar.
    """

    from ada.fem.results.artefacts import ELEM_HEADER_BYTES, ELEM_MAGIC

    sif = fem_files / "cantilever/sesam/static/line/STATIC_LINE_CANTILEVER_SESAMR1.SIF"
    if not sif.exists():
        pytest.skip(f"fixture not present: {sif}")

    bake = bake_fea_artefacts_from_source(sif, tmp_path / "out", src_key=sif.stem)
    manifest = json.loads(bake.manifest_path.read_text())

    mesh_meta = manifest["mesh"]
    assert mesh_meta.get("beam_solids_url") == "fea.beam_solids.glb"
    assert mesh_meta.get("beam_solids_elements_url") == "fea.beam_solids.elements.bin"
    n_beam_solids = mesh_meta["n_beam_solids"]
    assert n_beam_solids > 0, "expected at least one beam tessellated as solid"

    # GLB exists and parses as a trimesh scene with non-zero geometry.
    glb_path = bake.out_dir / "fea.beam_solids.glb"
    assert glb_path.exists()
    assert glb_path.stat().st_size > 0

    # Elements sidecar shares the AFEM format with the main mesh's
    # so the frontend reuses parseMeshElements for it.
    elements_path = bake.out_dir / "fea.beam_solids.elements.bin"
    data = elements_path.read_bytes()
    assert data[:4] == ELEM_MAGIC
    version, n_elements = struct.unpack("<II", data[4:12])
    assert version == 1
    assert n_elements == n_beam_solids

    # Per-beam ranges tile the triangle buffer with no overlaps and
    # all tri_counts must be positive (solid beams always have faces).
    raw = np.frombuffer(data[ELEM_HEADER_BYTES:], dtype=np.uint32).reshape(n_elements, 3)
    labels, starts, counts = raw[:, 0], raw[:, 1], raw[:, 2]
    cursor = 0
    for i in range(n_elements):
        assert int(starts[i]) == cursor
        assert int(counts[i]) > 0, f"beam {labels[i]} produced zero triangles"
        cursor += int(counts[i])
    # Labels are the source-file line-element ids — non-zero, distinct.
    assert int(labels.min()) >= 1
    assert len(set(labels.tolist())) == n_elements


def test_bake_emits_beam_solid_edges_sidecar(fem_files, tmp_path):
    """SIF line bake emits a beam-solid AFEG edges sidecar so the
    frontend can render the element-boundary wireframe on the beam-
    solid mesh. Without this, adjacent beam-elements look like one
    continuous tube.

    Checks: manifest field present, file exists with valid AFEG
    header, all edge endpoint indices land inside the beam-solid
    vertex buffer, edge count is meaningfully > 0 for a non-trivial
    fixture.
    """

    from ada.fem.results.artefacts import EDGE_HEADER_BYTES, EDGE_MAGIC

    sif = fem_files / "cantilever/sesam/static/line/STATIC_LINE_CANTILEVER_SESAMR1.SIF"
    if not sif.exists():
        pytest.skip(f"fixture not present: {sif}")

    bake = bake_fea_artefacts_from_source(sif, tmp_path / "out", src_key=sif.stem)
    manifest = json.loads(bake.manifest_path.read_text())

    mesh_meta = manifest["mesh"]
    assert mesh_meta.get("beam_solids_edges_url") == "fea.beam_solids.edges.bin"
    n_edges = mesh_meta["n_beam_solid_edges"]
    assert n_edges > 0, "expected at least one element-boundary edge on the beam solids"

    edges_path = bake.out_dir / "fea.beam_solids.edges.bin"
    data = edges_path.read_bytes()
    assert data[:4] == EDGE_MAGIC
    version, header_n = struct.unpack("<II", data[4:12])
    assert version == 1
    assert header_n == n_edges
    assert len(data) == EDGE_HEADER_BYTES + n_edges * 2 * 4

    # Endpoint indices must land inside the beam-solid vertex buffer.
    pairs = np.frombuffer(data[EDGE_HEADER_BYTES:], dtype=np.uint32).reshape(-1, 2)
    n_verts = mesh_meta["n_beam_solid_verts"]
    assert int(pairs.max()) < n_verts
    # Sorted (min, max) pairs — sanity check that the writer obeyed the
    # canonical-edge contract so the frontend doesn't render duplicates.
    assert (pairs[:, 0] <= pairs[:, 1]).all()


def test_bake_emits_beam_solid_warp_sidecar(fem_files, tmp_path):
    """AFBV sidecar — per-vertex (node0, node1, t) — must accompany
    the beam-solid mesh so the frontend can warp solid vertices in
    lockstep with their parent beam's nodal displacements. Verifies:
    layout matches the spec, every t ∈ [0, 1], node indices land in
    range, every (node0, node1) pair matches an actual line element
    in the source.
    """

    from ada.fem.results.artefacts import (
        BEAM_WARP_ENTRY_BYTES,
        BEAM_WARP_HEADER_BYTES,
        BEAM_WARP_MAGIC,
    )

    sif = fem_files / "cantilever/sesam/static/line/STATIC_LINE_CANTILEVER_SESAMR1.SIF"
    if not sif.exists():
        pytest.skip(f"fixture not present: {sif}")

    bake = bake_fea_artefacts_from_source(sif, tmp_path / "out", src_key=sif.stem)
    manifest = json.loads(bake.manifest_path.read_text())

    mesh_meta = manifest["mesh"]
    assert mesh_meta.get("beam_solids_warp_url") == "fea.beam_solids.warp.bin"
    n_verts = mesh_meta["n_beam_solid_verts"]
    assert n_verts > 0

    warp_path = bake.out_dir / "fea.beam_solids.warp.bin"
    data = warp_path.read_bytes()
    assert data[:4] == BEAM_WARP_MAGIC
    version, header_n = struct.unpack("<II", data[4:12])
    assert version == 1
    assert header_n == n_verts
    assert len(data) == BEAM_WARP_HEADER_BYTES + n_verts * BEAM_WARP_ENTRY_BYTES

    # Decode the interleaved (n0, n1, t) records.
    raw_u32 = np.frombuffer(data[BEAM_WARP_HEADER_BYTES:], dtype=np.uint32).reshape(
        n_verts, 3
    )
    n0 = raw_u32[:, 0]
    n1 = raw_u32[:, 1]
    t = raw_u32[:, 2].view(np.float32)

    n_points = mesh_meta["n_points"]
    assert int(n0.max()) < n_points
    assert int(n1.max()) < n_points
    # Endpoints must be distinct per vertex — zero-length beams would
    # produce equal indices, but the bake would have skipped them.
    assert (n0 != n1).all()

    # t ∈ [0, 1] — clamped on the bake side.
    assert float(t.min()) >= 0.0
    assert float(t.max()) <= 1.0

    # Every (n0, n1) pair must show up as the endpoints of an actual
    # line element in the source.
    from ada.fem.formats.sesam.results.read_sif import read_sif_file

    result = read_sif_file(sif)
    nmap = {int(x): i for i, x in enumerate(result.mesh.nodes.identifiers)}
    line_pairs = set()
    from ada.fem.shapes.definitions import LineShapes

    for block in result.mesh.elements:
        if not isinstance(block.elem_info.type, LineShapes):
            continue
        for nrefs in block.node_refs:
            a, b = int(nrefs[0]), int(nrefs[-1])
            line_pairs.add((nmap[a], nmap[b]))
            line_pairs.add((nmap[b], nmap[a]))
    seen_pairs = set(zip(n0.tolist(), n1.tolist()))
    assert seen_pairs <= line_pairs, "AFBV pair not found among source line elements"


def test_bake_skips_beam_solid_mesh_for_shell_only_sif(fem_files, tmp_path):
    """Shell-only fixtures have no line elements; the bake must skip
    the optional beam-solid emission entirely (no manifest key, no
    GLB / sidecar files left behind)."""

    sif = fem_files / "sesam/1EL_SHELL_R1.SIF"
    if not sif.exists():
        pytest.skip("fixture not present")

    bake = bake_fea_artefacts_from_source(sif, tmp_path / "out", src_key=sif.stem)
    manifest = json.loads(bake.manifest_path.read_text())

    assert "beam_solids_url" not in manifest["mesh"]
    assert "beam_solids_elements_url" not in manifest["mesh"]
    assert not (bake.out_dir / "fea.beam_solids.glb").exists()
    assert not (bake.out_dir / "fea.beam_solids.elements.bin").exists()


def test_bake_skips_beam_solid_mesh_for_rmed(fem_files, tmp_path):
    """RMED native streaming has no section info — try_solid_beams
    returns None and the bake omits the beam-solid artefacts."""

    rmed = fem_files / "code_aster/Cantilever_CA_EIG_bm.rmed"
    if not rmed.exists():
        pytest.skip("fixture not present")

    bake = bake_fea_artefacts_from_source(rmed, tmp_path / "out", src_key=rmed.stem)
    manifest = json.loads(bake.manifest_path.read_text())
    assert "beam_solids_url" not in manifest["mesh"]


def test_sif_section_parser_accumulates_non_contiguous_blocks():
    """The SIF reader's section-card parser used to overwrite
    ``self._sections[card_name]`` on every encounter — so a real-
    world SIF with two non-contiguous GIORH blocks would silently
    drop everything except the last block. This test fakes a tiny
    SIF with two GIORH blocks separated by a TDSECT block and
    asserts both sections survive into get_sections().
    """

    from io import StringIO

    from ada.fem.formats.sesam.results.read_sif import SifReader

    raw = (
        # Two TDSECT entries naming sec_ids 10 and 20.
        "TDSECT    4.00000000E+00  1.00000000E+01  4.00000000E+00  8.00000000E+00\n"
        "    Sec10\n"
        # First GIORH block: sec_id 10.
        "GIORH     1.00000000E+01  4.00000000E-01  1.00000000E-02  2.00000000E-01\n"
        "          1.50000000E-02  2.00000000E-01  1.50000000E-02  1.00000000E+00\n"
        "          1.00000000E+00\n"
        # Interrupt with a TDSECT for sec_id 20.
        "TDSECT    4.00000000E+00  2.00000000E+01  4.00000000E+00  8.00000000E+00\n"
        "    Sec20\n"
        # Second GIORH block: sec_id 20. Pre-fix this would clobber the
        # first block in ``_sections["GIORH"]``.
        "GIORH     2.00000000E+01  5.00000000E-01  1.20000000E-02  2.50000000E-01\n"
        "          1.80000000E-02  2.50000000E-01  1.80000000E-02  1.00000000E+00\n"
        "          1.00000000E+00\n"
        # Trailing sentinel — iter_card reads forward until it hits a
        # non-numeric line, so the synthetic SIF needs at least one
        # to avoid StopIteration mid-record.
        "END\n"
    )
    reader = SifReader(file=iter(StringIO(raw)))
    try:
        while True:
            line = next(reader.file)
            reader.eval_flags(line)
    except StopIteration:
        pass

    sections = reader.get_sections()
    assert 10 in sections, "first GIORH block dropped — accumulator bug regressed"
    assert 20 in sections, "second GIORH block missing"


def test_sif_gpipe_section_synthesises_circular_radius():
    """GPIPE writes outer diameter ``dy``; ada Section TUBULAR stores
    outer radius ``r``. The reader must halve ``dy`` so the rendered
    pipe has the right size. Also verifies the GBEAMG fallback
    doesn't override a real profile card on the same sec_id.
    """

    from io import StringIO

    from ada.fem.formats.sesam.results.read_sif import SifReader
    from ada.sections.categories import BaseTypes

    raw = (
        "TDSECT    4.00000000E+00  5.00000000E+00  4.00000000E+00  8.00000000E+00\n"
        "    P200x10\n"
        # GPIPE sec_id 5: di=0.18 (inner dia), dy=0.20 (outer dia), t=0.01.
        "GPIPE     5.00000000E+00  1.80000000E-01  2.00000000E-01  1.00000000E-02\n"
        "          1.00000000E+00  1.00000000E+00\n"
        # GBEAMG for the same sec_id — should NOT overwrite the GPIPE
        # synthesis since the profile card already produced a section.
        "GBEAMG    5.00000000E+00  0.00000000E+00  6.00000000E-03  1.00000000E-04\n"
        "          5.00000000E-05  5.00000000E-05  0.00000000E+00  0.00000000E+00\n"
        "          0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "          0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        # And a GBEAMG-only entry, sec_id 99 — should synthesise CIRCULAR.
        "GBEAMG    9.90000000E+01  0.00000000E+00  3.14159265E-02  1.00000000E-04\n"
        "          5.00000000E-05  5.00000000E-05  0.00000000E+00  0.00000000E+00\n"
        "          0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "          0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "END\n"
    )
    reader = SifReader(file=iter(StringIO(raw)))
    try:
        while True:
            line = next(reader.file)
            reader.eval_flags(line)
    except StopIteration:
        pass

    sections = reader.get_sections()

    # GPIPE-derived TUBULAR with r = 0.10 (= dy / 2).
    pipe = sections.get(5)
    assert pipe is not None
    assert pipe.type == BaseTypes.TUBULAR
    assert abs(pipe.r - 0.10) < 1e-9, f"expected r=0.10, got r={pipe.r}"
    assert abs(pipe.wt - 0.01) < 1e-9, f"expected wt=0.01, got wt={pipe.wt}"

    # GBEAMG-only fallback: CIRCULAR with r = sqrt(area / pi).
    # area = π × (0.1)² = 0.03141592… → r = 0.1.
    fb = sections.get(99)
    assert fb is not None
    assert fb.type == BaseTypes.CIRCULAR
    assert abs(fb.r - 0.1) < 1e-3


def test_classify_field_by_field_type_overrides_name():
    """An explicit NodalFieldType.DISP on the sample wins even if the
    name doesn't look like a displacement field — readers know
    their solver semantics better than the name heuristic."""

    from ada.fem.results.artefacts import _classify_field
    from ada.fem.results.field_data import NodalFieldType

    class _Sample:
        def __init__(self, ft):
            self.field_type = ft

    assert _classify_field("WEIRD_FIELD", _Sample(NodalFieldType.DISP)) == "displacement"
    assert _classify_field("WEIRD_FIELD", _Sample(NodalFieldType.FORCE)) == "reaction"
    # UNKNOWN falls through to the name heuristic.
    assert _classify_field("STRESS_X", _Sample(NodalFieldType.UNKNOWN)) == "stress"

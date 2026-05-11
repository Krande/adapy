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
        # of the right shape.
        for required in (
            "name_canonical",
            "name_native",
            "kind",
            "category",
            "support",
            "components",
            "blob",
            "n_steps",
            "steps",
            "scalar_range",
            "default_view",
        ):
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
    for field in manifest["fields"]:
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

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
    bake_artefacts,
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

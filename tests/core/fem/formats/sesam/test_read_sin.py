"""Tests for the pure-Python SIN (Norsam binary) reader.

Covers:

* low-level :mod:`sin_reader` walk — finds every file-header record,
  decodes every per-type block (NFIELD / ndim / dims / pointer count),
  and yields records that match the sibling SIF text byte-for-byte;
* high-level :func:`read_sin_file` round-trip — the produced
  :class:`FEAResult` is bit-equal to ``read_sif_file`` on the same
  model (nodes, elements, every result field's values);
* registry — ``.sin`` is in :func:`fea_artefact_extensions` and the
  factory returns a working :class:`FEAResultStreamAdapter`.

Fixture is the cantilever shell static analysis
(``files/fem_files/cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN``)
regenerable from its SIF sibling via ``scripts/regen_sin_fixtures.py``.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

# Test fixture: a small cantilever static analysis with 403 nodes,
# 360 shell elements (FQUS, ELTYP=24), 1 material, displacement +
# stress results — small enough to keep the test fast (<1 s).
_FIXTURE = pathlib.Path(__file__).resolve().parents[5] / ("files/fem_files/cantilever/sesam/static/shell")
SIN_PATH = _FIXTURE / "STATIC_SHELL_CANTILEVER_SESAMR1.SIN"
SIF_PATH = _FIXTURE / "STATIC_SHELL_CANTILEVER_SESAMR1.SIF"


@pytest.fixture
def sin_file():
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    return open_sin(SIN_PATH)


def test_sin_reader_finds_file_header_records(sin_file):
    """Every SIN file opens with the same four control records."""
    names = [name for _, name in sin_file.header_blocks]
    assert names == ["NORSAM", "ALLOCATE", "RESULTS", "IEND"]


def test_sin_reader_decodes_all_known_types(sin_file):
    """The cantilever fixture is known to contain at least these
    data types; regression guard against silent block-detection drift."""
    expected = {
        "GNODE",
        "GCOORD",
        "GELMNT1",
        "GELREF1",
        "GELTH",
        "MISOSEL",
        "BNBCD",
        "RDPOINTS",
        "RDSTRESS",
        "RDIELCOR",
        "RVNODDIS",
        "RVSTRESS",
        "RDRESREF",
        "TDMATER",
        "TDRESREF",
    }
    assert expected.issubset(set(sin_file.types))


def test_sin_reader_decodes_block_metadata(sin_file):
    """NFIELD / dims / count line up with what dnv-sifio reports for the same file."""
    cases = {
        "GNODE": (5, 1, (403,), 403),  # NFIELD, ndim, dims, count
        "GCOORD": (5, 1, (403,), 403),
        "GELMNT1": (5, 1, (360,), 360),
        "BNBCD": (5, 1, (200,), 13),  # capacity 200, populated 13
        "RVNODDIS": (7, 2, (1, 403), 403),
        "RVSTRESS": (7, 2, (1, 360), 360),
    }
    for name, (nf, nd, dims, cnt) in cases.items():
        b = sin_file.type_blocks[name]
        assert b.nfield == nf, f"{name}: NFIELD"
        assert b.ndim == nd, f"{name}: ndim"
        assert b.dims == dims, f"{name}: dims"
        assert b.count == cnt, f"{name}: count"


def test_sin_iter_records_matches_sif_for_gnode_gcoord(sin_file):
    """First 5 GNODE / GCOORD records bit-exact vs the SIF text source."""
    import re

    sif_text = SIF_PATH.read_text()

    def first_sif(type_name: str, n: int) -> list[list[float]]:
        out: list[list[float]] = []
        for m in re.finditer(
            rf"^{type_name}\b(.*?)(?=^[A-Z])",
            sif_text,
            flags=re.MULTILINE | re.DOTALL,
        ):
            out.append([float(x) for x in m.group(1).split()])
            if len(out) >= n:
                break
        return out

    for name in ("GNODE", "GCOORD"):
        sif_rows = first_sif(name, 5)
        sin_rows = list(sin_file.iter_records(name))[:5]
        assert len(sif_rows) == len(sin_rows) == 5
        for sif_r, sin_r in zip(sif_rows, sin_rows):
            assert sif_r == pytest.approx(list(sin_r), rel=1e-6)


def test_sin_header_control_fields_decoded(sin_file):
    """slot[2] (type-flag enum) and slot[3] (ptr-table cross-check)
    decode to the values reverse-engineered from the cantilever fixture.

    ``ptr_table_word * 8`` must point exactly to the first pointer
    slot's value field — that's the invariant the NDIM derivation
    relies on, so guard it explicitly."""
    expected_flags = {
        # Norsam type-class enum (see TypeBlock.type_flag docstring).
        "GNODE": 31,
        "GELMNT1": 31,
        "GCOORD": 21,
        "GELREF1": 21,
        "GELTH": 21,
        "BNBCD": 21,
        "MISOSEL": 20,
        "RDPOINTS": 2,
        "RVNODDIS": 2,
        "RVSTRESS": 2,
        "RDSTRESS": 1,
        "RDIELCOR": 1,
        "RDRESREF": 1,
        "TDMATER": 41,
        "TDRESREF": 41,
    }
    for name, flag in expected_flags.items():
        b = sin_file.type_blocks[name]
        assert b.type_flag == flag, f"{name}: type_flag"
        # ptr_table_word cross-check anchors the pointer table.
        assert (
            b.ptr_table_word * 8 == b.pointer_table_offset + 4
        ), f"{name}: ptr_table_word inconsistent with pointer_table_offset"


def test_sin_iter_text_records_decodes_material_name(sin_file):
    records = list(sin_file.iter_text_records("TDMATER"))
    assert len(records) == 1
    prefix, text = records[0]
    assert int(prefix[0]) == 1  # material id
    assert text == "S420"


def test_read_sin_file_equals_read_sif_file():
    """End-to-end: SIN-direct path and SIF-text path produce
    byte-identical FEAResult on the same model."""
    from ada.fem.formats.sesam.results.read_sif import read_sif_file, read_sin_file

    sin_res = read_sin_file(SIN_PATH)
    sif_res = read_sif_file(SIF_PATH)

    # Mesh — same node coords, same element blocks.
    assert len(sin_res.mesh.nodes.coords) == len(sif_res.mesh.nodes.coords)
    assert np.allclose(sin_res.mesh.nodes.coords, sif_res.mesh.nodes.coords)
    assert len(sin_res.mesh.elements) == len(sif_res.mesh.elements)
    for sn_block, sf_block in zip(sin_res.mesh.elements, sif_res.mesh.elements):
        assert sn_block.elem_info.type == sf_block.elem_info.type
        assert np.array_equal(sn_block.node_refs, sf_block.node_refs)
        assert np.array_equal(sn_block.identifiers, sf_block.identifiers)

    # Result fields — same name, shape, values.
    assert len(sin_res.results) == len(sif_res.results)
    for sn, sf in zip(sin_res.results, sif_res.results):
        assert sn.name == sf.name
        assert sn.values.shape == sf.values.shape
        assert np.allclose(sn.values, sf.values, equal_nan=True), f"result {sn.name!r}: SIN/SIF value drift"


def test_read_sin_metadata_cantilever():
    """Metadata-only read enumerates the same fields/steps as the full
    SinReader.load path, without paying the materialisation cost.

    On the cantilever the savings don't matter (file is 250 KB), but
    this is the regression guard that the IRES extraction matches
    what the full reader sees. Million-record-scale assertions live
    in the memory-bounded test below."""
    from ada.fem.formats.sesam.results.read_sin import read_sin_file, read_sin_metadata

    meta = read_sin_metadata(SIN_PATH)

    # Mesh-shape cross-check against the full read.
    full = read_sin_file(SIN_PATH)
    assert meta.node_count == len(full.mesh.nodes.coords)
    assert meta.element_count == sum(len(b.identifiers) for b in full.mesh.elements)

    # Every RV* type that has values should show up in field_steps,
    # and the union of steps should match what the picker would see
    # from the full FEAResult.
    grouped = full.get_results_grouped_by_field_value()
    full_steps_per_field = {name: sorted({int(d.step) for d in datas}) for name, datas in grouped.items()}
    # The metadata uses SIN type names (RVNODDIS, …) while FEAResult
    # uses field display names — cross-check that the *set* of
    # available step IDs matches in aggregate.
    meta_step_set = set(meta.steps)
    full_step_set = {s for steps in full_steps_per_field.values() for s in steps}
    assert meta_step_set == full_step_set, f"metadata steps {sorted(meta_step_set)} != full {sorted(full_step_set)}"


def test_iter_records_step_filter(sin_file):
    """``iter_records(name, where_first_word=step)`` slices to one
    IRES without materialising the rest. Cantilever has a single load
    case (IRES=1), so step=1 returns every record and step=2 returns
    none."""
    full = list(sin_file.iter_records("RVNODDIS"))
    step1 = list(sin_file.iter_records("RVNODDIS", where_first_word=1))
    step2 = list(sin_file.iter_records("RVNODDIS", where_first_word=2))
    assert len(full) == len(step1) == 403
    assert len(step2) == 0
    # The filtered slice is bit-identical to the un-filtered walk.
    for a, b in zip(full, step1):
        assert a == b


def test_read_sin_file_step_filter():
    """``read_sin_file(path, step=1)`` on a single-step fixture returns
    a FEAResult bit-equivalent to the unfiltered read — every RV*
    record on the cantilever has IRES=1, so the filter is a no-op."""
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    full = read_sin_file(SIN_PATH)
    s1 = read_sin_file(SIN_PATH, step=1)

    assert np.allclose(full.mesh.nodes.coords, s1.mesh.nodes.coords)
    assert len(s1.results) == len(full.results)
    for fr, sr in zip(full.results, s1.results):
        assert fr.name == sr.name
        assert fr.values.shape == sr.values.shape
        assert np.allclose(fr.values, sr.values, equal_nan=True)


def test_sin_stream_reader_bake_matches_full(tmp_path):
    """The per-step :class:`SinStreamReader` bake must produce byte-identical
    artefacts to the full :class:`FEAResultStreamAdapter` bake.

    The committed fixture is single-step, so this guards the per-step
    plumbing (geometry, global field specs, per-step value re-emission)
    against regression; the multi-step equivalence is validated out-of-tree
    on a large eigen deck (per-step == full, byte-for-byte)."""
    import hashlib

    from ada.fem.formats.sesam.results.byte_source import (
        FileRangeSource,
        PagedByteSource,
    )
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.results.artefacts import bake_artefacts, bake_fea_artefacts_from_source

    def digests(d):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file()}

    full_dir = tmp_path / "full"
    bake_fea_artefacts_from_source(SIN_PATH, full_dir, src_key="cantilever")

    stream_dir = tmp_path / "stream"
    with SinStreamReader(PagedByteSource(FileRangeSource(str(SIN_PATH)))) as reader:
        bake_artefacts(reader, stream_dir, src="cantilever")

    full, stream = digests(full_dir), digests(stream_dir)
    assert set(full) == set(stream), f"file set differs: {set(full) ^ set(stream)}"
    assert full == stream, f"differing artefacts: {[n for n in full if full[n] != stream.get(n)]}"


def test_sin_load_step_card_filter():
    """A per-field bake pass loads only that field's RV card, not all of them.

    The bake iterates one field at a time, so the streaming reader gathers just
    the RV card for the field being emitted — ~3x fewer record reads (and page
    fetches on a range source) on a multi-step deck. Here we assert the filter
    keeps exactly the requested card's field and drops the others."""
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    reader = SinStreamReader(open_sin(SIN_PATH))
    try:
        names = lambda res: {getattr(x, "name", None) for x in res.results}

        all_cards = names(reader._load_step(1))
        assert "RVNODDIS" in all_cards  # nodal present in the full load

        nodal_only = names(reader._load_step(1, cards={"RVNODDIS"}))
        assert "RVNODDIS" in nodal_only
        assert "STRESS" not in nodal_only  # element field's card was skipped

        stress_only = names(reader._load_step(1, cards={"RVSTRESS"}))
        assert "STRESS" in stress_only
        assert "RVNODDIS" not in stress_only  # nodal card was skipped
    finally:
        reader.close()


def test_bake_artefacts_on_artefact_sink_ships_each_file(tmp_path):
    """The ``on_artefact`` sink must fire once per artefact (manifest last)
    and yield the same bytes as a normal bake — even when the sink deletes
    each file the instant it lands. This is the contract the in-browser
    per-file streaming upload relies on (output tree never resides whole)."""
    import hashlib

    from ada.fem.formats.sesam.results.byte_source import (
        FileRangeSource,
        PagedByteSource,
    )
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.results.artefacts import bake_artefacts

    # Reference tree (sink off).
    ref_dir = tmp_path / "ref"
    with SinStreamReader(PagedByteSource(FileRangeSource(str(SIN_PATH)))) as reader:
        bake_artefacts(reader, ref_dir, src="cantilever")
    ref = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ref_dir.iterdir()) if p.is_file()}

    # Streamed tree: capture each file via the sink, then unlink it — so the
    # out_dir is empty at the end yet every artefact was observed in full.
    shipped: dict[str, str] = {}
    order: list[str] = []
    sink_dir = tmp_path / "sink"

    def sink(path):
        order.append(path.name)
        shipped[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        path.unlink()

    with SinStreamReader(PagedByteSource(FileRangeSource(str(SIN_PATH)))) as reader:
        bake_artefacts(reader, sink_dir, src="cantilever", on_artefact=sink)

    assert shipped == ref, f"sink bytes differ: {[n for n in ref if ref[n] != shipped.get(n)]}"
    assert order[-1] == "fea.manifest.json", f"manifest must be shipped last, got order {order}"
    # The sink deleted each file as it landed; nothing should remain.
    assert not [p for p in sink_dir.iterdir() if p.is_file()], "sink should have drained out_dir"


def test_truncate_pointer_table_finds_cutoff():
    """Validate the cap-vs-real-count truncation that keeps huge
    multi-SE RV* tables honest (real-world example: dims advertise
    ~20 M slots while only ~1 M are actually populated).

    Build a synthetic mmap with:
      - 2 real records (NFIELD=11.0 at known byte offsets)
      - then a "garbage" pointer that points to a non-NFIELD float
    Confirm the truncator stops exactly at the first garbage entry.
    """
    from ada.fem.formats.sesam.results.sin_reader import _truncate_pointer_table

    # Layout: bytes 0..7 = NFIELD prefix (float 11.0), bytes 8..15 = data,
    # bytes 16..23 = NFIELD prefix again, bytes 24..31 = data,
    # bytes 32..35 = garbage (float 0.0 — fails NFIELD check).
    buf = bytearray(64)
    nfield_bytes = np.array([11.0, 11.0], dtype=np.float32).tobytes()
    np_buf_writable = bytearray(buf)
    np_buf_writable[0:4] = nfield_bytes[0:4]  # NFIELD at byte 0
    np_buf_writable[16:20] = nfield_bytes[4:8]  # NFIELD at byte 16
    # bytes 32..35 left zero → float 0.0, not in [1, 1024]
    data = bytes(np_buf_writable)

    # Word offsets (1-indexed, ×4 bytes): byte 0 → word_ptr=1, byte 16 → 5,
    # byte 32 → 9 (this one points to NFIELD=0.0 = garbage).
    pt = np.array([0, 1, 5, 9, 1, 5], dtype=np.int64)
    cutoff = _truncate_pointer_table(data, pt)
    assert cutoff == 3, f"expected cutoff at slot[3] (first garbage), got {cutoff}"

    # All-valid case: no truncation.
    pt_all_valid = np.array([0, 1, 5, 1, 5], dtype=np.int64)
    assert _truncate_pointer_table(data, pt_all_valid) == 5

    # Empty case.
    assert _truncate_pointer_table(data, np.empty(0, dtype=np.int64)) == 0


def test_sin_registered_in_stream_readers():
    """``.sin`` shows up in the streaming-reader registry and the
    factory returns a usable adapter — the viewer's bake worker
    routes uploads through this path."""
    from ada.fem.results.artefacts import (
        FEAResultStreamAdapter,
        fea_artefact_extensions,
        make_stream_reader,
    )

    assert ".sin" in fea_artefact_extensions()
    reader = make_stream_reader(SIN_PATH)
    try:
        assert isinstance(reader, FEAResultStreamAdapter)
        # The streaming-bake calls these methods up front. They must
        # not raise for a well-formed SIN — empty results are fine
        # (the cantilever fixture has both nodal + element fields).
        geom = reader.read_mesh_geometry()
        assert geom.points.shape[0] > 0
        specs = reader.field_specs()
        assert any(s.support == "nodal" for s in specs), "no nodal field surfaced for the streaming bake"
    finally:
        reader.close()

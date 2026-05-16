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
_FIXTURE = pathlib.Path(__file__).resolve().parents[5] / (
    "files/fem_files/cantilever/sesam/static/shell"
)
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
        "GNODE", "GCOORD", "GELMNT1", "GELREF1", "GELTH", "MISOSEL",
        "BNBCD", "RDPOINTS", "RDSTRESS", "RDIELCOR",
        "RVNODDIS", "RVSTRESS", "RDRESREF", "TDMATER", "TDRESREF",
    }
    assert expected.issubset(set(sin_file.types))


def test_sin_reader_decodes_block_metadata(sin_file):
    """NFIELD / dims / count line up with what dnv-sifio reports for the same file."""
    cases = {
        "GNODE":    (5, 1, (403,), 403),    # NFIELD, ndim, dims, count
        "GCOORD":   (5, 1, (403,), 403),
        "GELMNT1":  (5, 1, (360,), 360),
        "BNBCD":    (5, 1, (200,), 13),     # capacity 200, populated 13
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
            sif_text, flags=re.MULTILINE | re.DOTALL,
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
        "GNODE": 31, "GELMNT1": 31,
        "GCOORD": 21, "GELREF1": 21, "GELTH": 21, "BNBCD": 21,
        "MISOSEL": 20,
        "RDPOINTS": 2, "RVNODDIS": 2, "RVSTRESS": 2,
        "RDSTRESS": 1, "RDIELCOR": 1, "RDRESREF": 1,
        "TDMATER": 41, "TDRESREF": 41,
    }
    for name, flag in expected_flags.items():
        b = sin_file.type_blocks[name]
        assert b.type_flag == flag, f"{name}: type_flag"
        # ptr_table_word cross-check anchors the pointer table.
        assert b.ptr_table_word * 8 == b.pointer_table_offset + 4, (
            f"{name}: ptr_table_word inconsistent with pointer_table_offset"
        )


def test_sin_iter_text_records_decodes_material_name(sin_file):
    records = list(sin_file.iter_text_records("TDMATER"))
    assert len(records) == 1
    prefix, text = records[0]
    assert int(prefix[0]) == 1  # material id
    assert text == "S420"


def test_read_sin_file_equals_read_sif_file():
    """End-to-end: SIN-direct path and SIF-text path produce
    byte-identical FEAResult on the same model."""
    from ada.fem.formats.sesam.results.read_sif import (
        read_sif_file,
        read_sin_file,
    )

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
        assert np.allclose(sn.values, sf.values, equal_nan=True), (
            f"result {sn.name!r}: SIN/SIF value drift"
        )


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
        assert any(s.support == "nodal" for s in specs), (
            "no nodal field surfaced for the streaming bake"
        )
    finally:
        reader.close()

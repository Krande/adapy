"""Tests for verification/filters.py block-sugar handlers.

The verification report registers a ``fea_modes_compare`` figure-source
that expands a comment block into the results appendix for one family of
cases: one section per mesh configuration, one heading per mode, and the
formats side by side in a grid div under it. The filter walks
``doc_root/<assets_dir>/`` for baked FEA bundles and returns a mixed list
of ``MarkdownChunk`` (headings, grid fences, placeholders) and
``RenderResult`` (one figure per solver per mode).

We don't exercise the renderer or paradoc's full compile here — pure
unit tests against ``FeaModesCompareFilter.render()``. Synth a minimal
bundle layout on disk, drive the filter directly, assert the returned
sequence.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

pytest.importorskip("paradoc.figure_sources")

# Make verification/ importable. The report dir doesn't sit on sys.path
# in normal builds (paradoc loads tasks.py via spec_from_file_location);
# tests bring it in explicitly so `import filters` resolves.
_VERIFICATION_DIR = pathlib.Path(__file__).resolve().parents[2] / "verification"
if str(_VERIFICATION_DIR) not in sys.path:
    sys.path.insert(0, str(_VERIFICATION_DIR))


# 1x1 valid PNG — used so the assets_from_bundle_dir poster-walk picks
# up the files as real PNGs (it filenames-globs but the file content
# should be valid bytes if anything else inspects them).
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
    b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9c"
    b"c\x00\x01\x00\x00\x05\x00\x01\r\n\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_case(case_dir: pathlib.Path, *, modes: list[int]) -> None:
    """Write a minimal FEA bundle: manifest + mesh GLB + per-mode PNG posters.

    ``modes`` is a list of 1-based mode numbers to materialise as
    ``fea.mesh.mode_N.png``. Mode 1 is also written as ``fea.mesh.png``
    (the canonical poster naming convention :func:`assets_from_bundle_dir`
    walks).
    """
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "fea.manifest.json").write_text(json.dumps({"fields": []}))
    (case_dir / "fea.mesh.glb").write_bytes(b"fake-glb-bytes")
    if 1 in modes:
        (case_dir / "fea.mesh.png").write_bytes(_PNG_BYTES)
    for n in modes:
        if n >= 2:
            (case_dir / f"fea.mesh.mode_{n}.png").write_bytes(_PNG_BYTES)


def _render(tmp_path, case_prefix: str, analysis: str = "eigen") -> list:
    from filters import FeaModesCompare, FeaModesCompareFilter

    spec = FeaModesCompare(
        figure_source="fea_modes_compare",
        figure_title="x",
        case_prefix=case_prefix,
        analysis=analysis,
    )
    return FeaModesCompareFilter(bundle_root=tmp_path, doc_root=tmp_path).render(spec, key="fea_modes_compare_1")


def _texts(out) -> list[str]:
    from paradoc.figure_sources.filters.base import MarkdownChunk

    return [e.text for e in out if isinstance(e, MarkdownChunk)]


def test_formats_side_by_side_per_mode(tmp_path):
    """Two formats of the same configuration land under one heading, and each mode's figures sit in
    one grid div in the fixed solver order (Calculix before Code_Aster)."""
    from paradoc.figure_sources.filters.base import MarkdownChunk, RenderResult

    _make_case(tmp_path / "_assets" / "cantilever_EIG_ca_shell_o1_hqTrue_riFalse", modes=[1, 2])
    _make_case(tmp_path / "_assets" / "cantilever_EIG_ccx_shell_o1_hqTrue_riFalse", modes=[1, 2])

    out = _render(tmp_path, "cantilever_EIG")

    # heading + [mode heading, grid open, ccx, ca, grid close] x 2
    assert len(out) == 1 + 5 * 2
    assert "### Shell, 1st order, QUAD" in out[0].text
    for m in range(2):
        block = out[1 + 5 * m : 1 + 5 * (m + 1)]
        assert f"#### Mode {m + 1}" in block[0].text
        assert block[1].text.startswith(":::") and "grid" in block[1].text
        assert block[4].text == ":::"
        figs = block[2:4]
        assert all(isinstance(f, RenderResult) for f in figs)
        assert [f.metadata["fea_bundle_key"].split("_")[2] for f in figs] == ["ccx", "ca"]
        assert all(f.metadata["fea_mode_index"] == m for f in figs)
        assert figs[0].caption.startswith("Calculix")
        assert pathlib.Path(figs[0].png_path).is_absolute() and pathlib.Path(figs[0].png_path).is_file()
    assert not any(isinstance(e, MarkdownChunk) and "Calculix" in e.text for e in out)


def test_configurations_get_their_own_sections(tmp_path):
    """Different mesh configurations do not share a heading."""
    _make_case(tmp_path / "_assets" / "cantilever_EIG_ca_solid_o1_hqFalse_riFalse", modes=[1])
    _make_case(tmp_path / "_assets" / "cantilever_EIG_ccx_solid_o2_hqTrue_riTrue", modes=[1])

    headings = [t for t in _texts(_render(tmp_path, "cantilever_EIG")) if t.strip().startswith("### ")]
    assert [h.strip() for h in headings] == [
        "### Solid, 1st order, TET",
        "### Solid, 2nd order, HEX, reduced integration",
    ]


def test_prefix_selects_the_case_family(tmp_path):
    """The plate prefix does not pick up cantilever cases (and vice versa), and nested bundle dirs
    such as ``_assets/plate/`` are found."""
    from paradoc.figure_sources.filters.base import RenderResult

    _make_case(tmp_path / "_assets" / "cantilever_EIG_ca_shell_o1_hqTrue_riFalse", modes=[1])
    _make_case(tmp_path / "_assets" / "plate" / "plate_EIG_ca_shell_o1_stFalse", modes=[1, 2])
    _make_case(tmp_path / "_assets" / "plate" / "plate_static_ca_shell_o1_stTrue_h0p0625", modes=[1])

    keys = {e.metadata["fea_bundle_key"] for e in _render(tmp_path, "plate_EIG") if isinstance(e, RenderResult)}
    assert keys == {"plate_EIG_ca_shell_o1_stFalse"}


def test_static_analysis_has_no_mode_heading(tmp_path):
    _make_case(tmp_path / "_assets" / "plate" / "plate_static_ca_shell_o1_stTrue_h0p0625", modes=[1])
    _make_case(tmp_path / "_assets" / "plate" / "plate_static_ccx_shell_o1_stTrue_h0p0625", modes=[1])

    out = _render(tmp_path, "plate_static", analysis="static")
    texts = _texts(out)
    assert "### Shell, 1st order, stiffened, seed 0.0625 m" in texts[0]
    assert not any("#### Mode" in t for t in texts)
    assert sum(t.startswith(":::") and "grid" in t for t in texts) == 1


def test_case_without_manifest_is_named_unavailable(tmp_path):
    """A case dir without a baked manifest does not drop the configuration silently: the formats
    that did bake render, and the missing one is named."""
    from paradoc.figure_sources.filters.base import RenderResult

    _make_case(tmp_path / "_assets" / "cantilever_EIG_ccx_solid_o1_hqFalse_riFalse", modes=[1])
    bare = tmp_path / "_assets" / "cantilever_EIG_ca_solid_o1_hqFalse_riFalse"
    bare.mkdir(parents=True)
    (bare / "mode_01.glb").write_bytes(b"x")

    out = _render(tmp_path, "cantilever_EIG")
    assert any("unavailable" in t and "Code_Aster" in t for t in _texts(out))
    assert [e.caption.split(" ")[0] for e in out if isinstance(e, RenderResult)] == ["Calculix"]


def test_no_matching_cases_returns_placeholder_chunk(tmp_path):
    from paradoc.figure_sources.filters.base import MarkdownChunk

    _make_case(tmp_path / "_assets" / "cantilever_EIG_ca_solid_o1_hqFalse_riFalse", modes=[1])
    out = _render(tmp_path, "plate_EIG")
    assert len(out) == 1
    assert isinstance(out[0], MarkdownChunk)
    assert "plate_EIG" in out[0].text

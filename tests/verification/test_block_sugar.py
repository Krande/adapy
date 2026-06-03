"""Tests for verification/filters.py block-sugar handlers.

The verification report registers an ``eig_modes_section`` figure-source
that expands a comment block into the per-case markdown sections for one
solver. The filter walks ``bundle_root/<assets_dir>/`` for baked FEA
bundles, filters by solver, and returns a mixed list of
``MarkdownChunk`` (headings, placeholders) and ``RenderResult`` (per-mode
figure references).

We don't exercise the renderer or paradoc's full compile here — pure
unit tests against ``EigModesSectionFilter.render()``. Synth a minimal
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


def test_renders_per_case_with_mode_per_section_layout(tmp_path):
    """A baked CA case yields:

    1. ``MarkdownChunk('### <case>\\n')`` heading
    2. Per baked mode: ``MarkdownChunk('#### Mode N\\n')`` then ``RenderResult``.
    """
    from filters import EigModesSection, EigModesSectionFilter
    from paradoc.figure_sources.filters.base import MarkdownChunk, RenderResult

    case_dir = tmp_path / "_assets" / "cantilever_EIG_ca_solid_o1_hqFalse_riFalse"
    _make_case(case_dir, modes=[1, 2, 3])

    spec = EigModesSection(
        figure_source="eig_modes_section",
        figure_title="Code Aster modes",
        solver="code_aster",
        layout="mode_per_section",
    )
    filt = EigModesSectionFilter(bundle_root=tmp_path, doc_root=tmp_path)
    out = filt.render(spec, key="eig_modes_section_1")

    # Sequence: case heading + [mode heading + render] × 3
    assert len(out) == 1 + 2 * 3
    assert isinstance(out[0], MarkdownChunk)
    assert "### cantilever_EIG_ca_solid_o1_hqFalse_riFalse" in out[0].text

    for i, mode_n in enumerate([1, 2, 3]):
        chunk = out[1 + 2 * i]
        result = out[1 + 2 * i + 1]
        assert isinstance(chunk, MarkdownChunk)
        assert f"#### Mode {mode_n}" in chunk.text
        assert isinstance(result, RenderResult)
        assert result.caption.endswith(f"mode {mode_n}")
        assert result.metadata["fea_mode_index"] == mode_n - 1
        assert result.metadata["fea_bundle_key"].startswith("cantilever_EIG_ca_")
        # png_path is absolute (filter reads from doc_root, not bundle_root).
        assert pathlib.Path(result.png_path).is_absolute()
        assert pathlib.Path(result.png_path).is_file()


def test_skips_cases_for_other_solvers(tmp_path):
    """Only cases matching the solver tag should appear in the output."""
    from filters import EigModesSection, EigModesSectionFilter
    from paradoc.figure_sources.filters.base import RenderResult

    # One CA case + one CCX case in the assets dir
    _make_case(
        tmp_path / "_assets" / "cantilever_EIG_ca_solid_o1_hqFalse_riFalse",
        modes=[1, 2],
    )
    _make_case(
        tmp_path / "_assets" / "cantilever_EIG_ccx_solid_o1_hqFalse_riFalse",
        modes=[1, 2],
    )

    spec = EigModesSection(
        figure_source="eig_modes_section",
        figure_title="CA modes",
        solver="code_aster",
        layout="mode_per_section",
    )
    out = EigModesSectionFilter(bundle_root=tmp_path, doc_root=tmp_path).render(spec, key="eig_modes_section_1")

    case_keys = {e.metadata["fea_bundle_key"] for e in out if isinstance(e, RenderResult)}
    assert case_keys == {"cantilever_EIG_ca_solid_o1_hqFalse_riFalse"}


def test_no_matching_solver_returns_placeholder_chunk(tmp_path):
    """When no case matches the solver, a single placeholder chunk is
    emitted (informative — distinguishes 'wrong solver' from 'empty
    assets dir')."""
    from filters import EigModesSection, EigModesSectionFilter
    from paradoc.figure_sources.filters.base import MarkdownChunk

    _make_case(
        tmp_path / "_assets" / "cantilever_EIG_ca_solid_o1_x",
        modes=[1],
    )

    spec = EigModesSection(
        figure_source="eig_modes_section",
        figure_title="x",
        solver="abaqus",
        layout="mode_per_section",
    )
    out = EigModesSectionFilter(bundle_root=tmp_path, doc_root=tmp_path).render(spec, key="eig_modes_section_1")
    assert len(out) == 1
    assert isinstance(out[0], MarkdownChunk)
    assert "abaqus" in out[0].text


def test_case_dir_without_manifest_emits_placeholder(tmp_path):
    """Committed mode-GLBs without a baked manifest (the cache-only state
    today) yields the 'figures unavailable' placeholder for that case —
    the report reader sees the case exists but has no figures yet."""
    from filters import EigModesSection, EigModesSectionFilter
    from paradoc.figure_sources.filters.base import MarkdownChunk

    case_dir = tmp_path / "_assets" / "cantilever_EIG_ca_solid_o1_x"
    case_dir.mkdir(parents=True)
    # No fea.manifest.json — only the per-mode GLBs the repo commits.
    (case_dir / "mode_01.glb").write_bytes(b"x")

    spec = EigModesSection(
        figure_source="eig_modes_section",
        figure_title="x",
        solver="code_aster",
        layout="mode_per_section",
    )
    out = EigModesSectionFilter(bundle_root=tmp_path, doc_root=tmp_path).render(spec, key="eig_modes_section_1")
    # Single placeholder chunk: case heading + "_unavailable_" line.
    assert len(out) == 1
    assert isinstance(out[0], MarkdownChunk)
    assert "### cantilever_EIG_ca_solid_o1_x" in out[0].text
    assert "Mode-shape figures unavailable" in out[0].text


def test_gallery_layout_omits_per_mode_subsection_headings(tmp_path):
    """layout=gallery drops the `#### Mode N` chunks but keeps the case
    heading + per-mode RenderResults (visual grouping is a v1
    follow-up)."""
    from filters import EigModesSection, EigModesSectionFilter
    from paradoc.figure_sources.filters.base import MarkdownChunk, RenderResult

    _make_case(
        tmp_path / "_assets" / "cantilever_EIG_ca_solid_o1_x",
        modes=[1, 2, 3],
    )
    spec = EigModesSection(
        figure_source="eig_modes_section",
        figure_title="gallery",
        solver="code_aster",
        layout="gallery",
    )
    out = EigModesSectionFilter(bundle_root=tmp_path, doc_root=tmp_path).render(spec, key="eig_modes_section_1")

    chunk_texts = [e.text for e in out if isinstance(e, MarkdownChunk)]
    results = [e for e in out if isinstance(e, RenderResult)]

    # One chunk: the case heading. No "#### Mode N" subsection headings.
    assert len(chunk_texts) == 1
    assert "### cantilever_EIG_ca_solid_o1_x" in chunk_texts[0]
    assert not any("#### Mode" in t for t in chunk_texts)
    # All three mode figures still present.
    assert len(results) == 3

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


@pytest.fixture(autouse=True)
def _isolated_snapshots(tmp_path, monkeypatch):
    """The per-mode tables read each case's JSON snapshot; point them at an empty cache of the test's own,
    so what a test sees doesn't depend on which snapshots the checkout's verification/.cache holds."""
    import filters

    monkeypatch.setattr(filters, "_CACHE_DIR", tmp_path / ".cache")
    monkeypatch.setattr(filters, "_PLATE_CACHE_DIR", tmp_path / ".cache-plate")


def _write_snapshot(tmp_path, case_key: str, modes: dict[int, dict]) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir(exist_ok=True)
    payload = {"name": case_key, "eigen_mode_data": {str(n): {"no": n, **fields} for n, fields in modes.items()}}
    (cache / f"{case_key}.json").write_text(json.dumps(payload))


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


def test_each_mode_gets_a_table_of_its_data_below_the_figures(tmp_path):
    """Below a mode's figures: a row per solver with its frequency, eigenvalue, effective masses and
    mass-normalised participation factors, read from the case's snapshot."""
    ca, ccx = "cantilever_EIG_ca_shell_o1_hqTrue_riFalse", "cantilever_EIG_ccx_shell_o1_hqTrue_riFalse"
    _make_case(tmp_path / "_assets" / ca, modes=[1, 2])
    _make_case(tmp_path / "_assets" / ccx, modes=[1, 2])
    _write_snapshot(tmp_path, ca, {1: {"f_hz": 13.146, "efy": 119.92, "py": 10.951}, 2: {"f_hz": 19.972}})
    _write_snapshot(tmp_path, ccx, {1: {"f_hz": 12.764, "eigenvalue": 6431.71, "efy": 119.9, "py": -0.5}})

    texts = _texts(_render(tmp_path, "cantilever_EIG"))
    tables = [t for t in texts if t.strip().startswith("| Solver |")]
    assert len(tables) == 2  # one per mode

    mode1 = tables[0].strip().splitlines()
    assert mode1[0] == "| Solver | f [Hz] | λ [rad²/s²] | Meff Y [kg] | Γ Y |"  # unreported quantities left out
    assert mode1[2] == "| Calculix | 12.764 | 6431.71 | 119.90 | -10.950 |"  # sign(Γ)·√Meff
    assert mode1[3] == "| Code_Aster | 13.146 | – | 119.92 | 10.951 |"
    # Calculix reported nothing for mode 2: only Code_Aster's row
    assert [r.split("|")[1].strip() for r in tables[1].strip().splitlines()[2:]] == ["Code_Aster"]


def test_the_table_follows_the_grid_and_needs_a_snapshot(tmp_path):
    from paradoc.figure_sources.filters.base import MarkdownChunk

    key = "cantilever_EIG_ca_shell_o1_hqTrue_riFalse"
    _make_case(tmp_path / "_assets" / key, modes=[1])
    assert not any("| Solver |" in t for t in _texts(_render(tmp_path, "cantilever_EIG")))

    _write_snapshot(tmp_path, key, {1: {"f_hz": 13.1}})
    out = _render(tmp_path, "cantilever_EIG")
    close = next(i for i, e in enumerate(out) if isinstance(e, MarkdownChunk) and e.text == ":::")
    assert out[close + 1].text.strip().startswith("| Solver | f [Hz] |")


def test_static_analysis_has_no_mode_table(tmp_path):
    key = "plate_static_ca_shell_o1_stTrue_h0p0625"
    _make_case(tmp_path / "_assets" / "plate" / key, modes=[1])
    _write_snapshot(tmp_path, key, {1: {"f_hz": 1.0}})
    assert not any("| Solver |" in t for t in _texts(_render(tmp_path, "plate_static", analysis="static")))


def test_a_value_too_small_for_the_column_prints_in_scientific_notation():
    from filters import _fmt

    assert _fmt(-1.234e-14, ".3f") == "-1.23e-14"
    assert _fmt(0.0004, ".3f") == "4.00e-04"
    assert _fmt(0.0, ".3f") == "0.000"
    assert _fmt(-0.25, ".3f") == "-0.250"
    assert _fmt(None, ".3f") == "–"

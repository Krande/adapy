"""Tests for the ``ada.fem.results.docs`` paradoc bridge.

Covers the descriptor (:class:`FeaDocAssets`), the bake adapter
(:func:`assets_for_docs`), the ThreeDData row factory
(:func:`to_paradoc_rows`), and the stock paradoc Filter subclass
(:class:`FeaCaseFilter`). The smoke tests share a single RMED fixture
to keep wall-time low — the renderer machinery is the heavy bit and
gets exercised in ``test_fea_artefacts.py``'s
``test_bake_with_posters_renders_requested_modes``.
"""

from __future__ import annotations

import pathlib

import pytest

# Skip the whole module when paradoc isn't installed in the env —
# `ada.fem.results.docs` imports paradoc at module load. The artefact
# tests don't need this skip because they live in `ada.fem.results.artefacts`
# directly.
pytest.importorskip("paradoc.filters")
pytest.importorskip("paradoc.db.models")


RMED_FIXTURE = "code_aster/Cantilever_CA_EIG_bm.rmed"


@pytest.fixture
def rmed_path(fem_files):
    p = fem_files / RMED_FIXTURE
    if not p.exists():
        pytest.skip(f"RMED fixture not present: {RMED_FIXTURE}")
    return p


def test_assets_for_docs_from_path_collects_frequencies(rmed_path, tmp_path):
    """Path-driven entry point bakes the bundle, renders posters
    (here ``modes=2`` for speed), and pulls per-mode frequencies out of
    the manifest. Solver / version come back as ``None`` because the
    bake-from-path path doesn't carry an in-memory FEAResult to read
    them off — those populate via the FEAResult-driven test below."""

    pytest.importorskip("pygfx")
    pytest.importorskip("trimesh")
    from ada.fem.results.docs import assets_for_docs

    out = tmp_path / "bundle"
    assets = assets_for_docs(rmed_path, key="ca_bm", out_dir=out, modes=2)

    assert assets.key == "ca_bm"
    assert assets.bundle_dir == out
    assert assets.manifest_path.is_file()
    assert assets.mesh_glb_path.is_file()
    assert assets.n_modes == 2
    assert set(assets.poster_paths.keys()) == {0, 1}
    assert assets.canonical_poster_path is not None
    assert assets.canonical_poster_path.name == "fea.mesh.png"
    assert assets.solver is None  # path-driven — no FEAResult
    assert assets.solver_version is None
    # Cantilever_CA_EIG_bm has 20 modes; the manifest lists all 20
    # eigenfrequencies regardless of how many posters we rendered.
    assert assets.frequencies is not None and len(assets.frequencies) == 20
    assert all(f > 0 for f in assets.frequencies)


def test_assets_for_docs_from_fea_result_populates_solver(rmed_path, tmp_path):
    """Driving from an in-memory ``FEAResult`` pulls solver name +
    version off the result object so the descriptor's ``solver`` field
    is populated for downstream filter rendering."""

    pytest.importorskip("pygfx")
    pytest.importorskip("trimesh")
    from ada.fem.formats.code_aster.results.read_rmed_results import (
        read_rmed_file,
    )
    from ada.fem.results.docs import assets_for_docs

    res = read_rmed_file(rmed_path)
    assets = assets_for_docs(
        res, key="ca_bm", out_dir=tmp_path / "bundle", modes=1,
    )
    # FEATypes enum's `.value`.
    assert assets.solver == "code_aster"


def test_to_paradoc_rows_emits_canonical_plus_mode_views(rmed_path, tmp_path):
    """One canonical row + N mode-view rows, all paths relative to the
    supplied ``base_dir``, all sharing the same mesh GLB. Per-mode
    rows carry the right ``fea_mode_index`` so the embed picks the
    correct displacement step at mount time."""

    pytest.importorskip("pygfx")
    pytest.importorskip("trimesh")
    from ada.fem.results.docs import assets_for_docs, to_paradoc_rows

    base = tmp_path
    assets = assets_for_docs(
        rmed_path, key="ca_bm", out_dir=base / "ca_bm", modes=3,
    )
    rows = to_paradoc_rows(assets, base_dir=base)

    assert len(rows) == 4  # 1 canonical + 3 mode-view
    canon = rows[0]
    assert canon.key == "ca_bm"
    assert canon.source_type == "fea_artefact_bundle"
    assert canon.metadata["fea_bundle_dir"] == "ca_bm"
    assert canon.metadata["fea_manifest_path"] == "ca_bm/fea.manifest.json"

    mode_rows = rows[1:]
    keys = [r.key for r in mode_rows]
    assert keys == ["ca_bm_mode_1", "ca_bm_mode_2", "ca_bm_mode_3"]
    for i, r in enumerate(mode_rows):
        assert r.source_type == "fea_artefact_bundle_mode_view"
        assert r.metadata["fea_bundle_key"] == "ca_bm"
        assert r.metadata["fea_mode_index"] == i
        # Paths must be bundle-relative — paradoc's static export
        # walks them off ``base_dir``, an absolute path would break
        # the copy step.
        assert not pathlib.Path(r.glb_path).is_absolute()
        assert not pathlib.Path(r.metadata["image_path"]).is_absolute()


def test_feacase_filter_lazy_bake_and_modes(rmed_path, tmp_path):
    """``FeaCaseFilter`` doesn't run the bake until the first attr is
    accessed. Subsequent accesses reuse the cached :class:`FeaDocAssets`.
    ``mode_<N>`` resolves to a :class:`paradoc.filters.ThreeDView`
    matching :func:`to_paradoc_rows`' glb_key convention.

    Modes are pre-attached at class level (paradoc's filter cache
    walks ``inspect.getsource(getattr(cls, name))`` which bypasses
    ``__getattr__``), so ``mode_<N>`` exists for every N in
    ``1..MAX``; whether the mode was actually baked governs whether
    the view carries a poster path. The static ``ThreeDView.glb_key``
    is consistent regardless — paradoc renders a placeholder when the
    matching ``ThreeDData`` row isn't registered.
    """

    pytest.importorskip("pygfx")
    pytest.importorskip("trimesh")
    from ada.fem.results.docs import FeaCaseFilter, _MAX_MODE_ATTRS

    case = FeaCaseFilter("ca_bm", rmed_path, tmp_path / "bundle", modes=2)
    assert case._assets is None  # lazy

    canonical = case.canonical()
    assert canonical.glb_key == "ca_bm"
    assert case._assets is not None  # bake fired
    cached = case._assets
    _ = case.solver()
    assert case._assets is cached  # not re-baked

    # Baked mode → view carries the per-mode poster path.
    mode2 = case.mode_2()
    assert mode2.glb_key == "ca_bm_mode_2"
    assert mode2.caption.endswith("mode 2.")
    assert mode2.image_path is not None and mode2.image_path.is_file()

    # Un-baked mode → view still resolves with the right glb_key, but
    # ``image_path`` is None. paradoc's static export shows a
    # placeholder; the interactive bundle viewer still mounts and
    # surfaces the mode through SimulationControls because the bundle
    # carries every step's displacement blob regardless of poster.
    mode_unbaked = case.mode_5()
    assert mode_unbaked.glb_key == "ca_bm_mode_5"
    assert mode_unbaked.image_path is None

    # mode_<MAX_MODE_ATTRS> exists; mode_<MAX_MODE_ATTRS+1> doesn't —
    # the class-level cap is intentional (see _MAX_MODE_ATTRS docstring).
    assert hasattr(case, f"mode_{_MAX_MODE_ATTRS}")
    with pytest.raises(AttributeError):
        getattr(case, f"mode_{_MAX_MODE_ATTRS + 1}")

    # mode_<N> is @attr-decorated at class level so paradoc's resolver
    # picks it up the same way as the static @attr methods.
    raw = FeaCaseFilter.__dict__["mode_2"]
    assert getattr(raw, "__paradoc_attr__", False) is True


def test_register_paradoc_block_sugar_is_callable():
    """The entry-point target must exist and be callable so paradoc's
    discovery hook (step 6) doesn't fail at startup. The function is
    a no-op stub until step 6 wires the block sugar in."""

    from ada.fem.results.docs import register_paradoc_block_sugar

    class _Dispatcher:
        pass

    # No-op stub today — must not raise.
    register_paradoc_block_sugar(_Dispatcher())


def test_paradoc_figure_sources_entry_point_registered():
    """When adapy is installed (dist-info present) the entry-point
    declared in ``pyproject.toml`` must surface under the
    ``paradoc.figure_sources`` group and resolve back to
    :func:`register_paradoc_block_sugar`.

    The verification report runs adapy via PYTHONPATH rather than a
    pip install, in which case there's no dist-info and discovery
    returns nothing — that's a legitimate dev environment, not a
    failure, so we skip rather than fail.
    """

    from importlib import metadata

    try:
        dist = metadata.distribution("ada-py")
    except metadata.PackageNotFoundError:
        pytest.skip(
            "ada-py not installed in this env (running off PYTHONPATH); "
            "entry-point discovery only fires for installed packages."
        )

    eps = metadata.entry_points(group="paradoc.figure_sources")
    by_name = {ep.name: ep for ep in eps if ep.dist == dist}
    if "fea_artefact_bundle" not in by_name:
        # The conda-forge feedstock or pip-installed wheel pre-dates the
        # entry-point declaration. Skip rather than fail: that's the
        # expected steady state until a fresh build lands. See
        # ``dap/notes/conda_forge_adapy_recipe.md`` for the rebuild
        # checklist when entry-points change.
        pytest.skip(
            "fea_artefact_bundle entry-point not present in the "
            "installed ada-py dist-info — rebuild the wheel "
            "(`pip install -e .`) or refresh the conda-forge feedstock."
        )

    from ada.fem.results.docs import register_paradoc_block_sugar

    assert by_name["fea_artefact_bundle"].load() is register_paradoc_block_sugar

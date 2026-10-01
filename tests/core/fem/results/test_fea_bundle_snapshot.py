"""Committed bundle snapshots: the mode shapes of a solver the doc build cannot run.

A licensed machine bakes a lean bundle beside the case's JSON snapshot; any other build copies it
into its assets dir and renders the posters there. Posters are stubbed out here -- the renderer
needs a GPU adapter, and what is under test is which files move where.
"""

import json

import numpy as np
import pytest

pytest.importorskip("paradoc")

from ada.fem.results import FeaCaseResult, walk_cached_case_results  # noqa: E402
from ada.fem.results.docs import restore_fea_bundles, snapshot_fea_bundle  # noqa: E402

POINTS = np.array(
    [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [2.0, 1.0, 0.0]]
)
KEY = "cantilever_EIG_aba_shell_o1_hqTrue_riFalse"


def _eigen_result(n_modes=3):
    """A two-quad plate with ``n_modes`` bending modes."""
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import (
        ElementBlock,
        ElementInfo,
        FEAResult,
        FemNodes,
        Mesh,
    )
    from ada.fem.results.field_data import NodalFieldData, NodalFieldType
    from ada.fem.shapes.definitions import ShellShapes

    ids = np.arange(1, 7)
    bending = np.column_stack([np.zeros(6), np.zeros(6), (POINTS[:, 0] / 2.0) ** 2])
    block = ElementBlock(
        elem_info=ElementInfo(type=ShellShapes.QUAD, source_software=FEATypes.ABAQUS, source_type="S4"),
        node_refs=np.array([[1, 2, 5, 4], [2, 3, 6, 5]]),
        identifiers=np.array([1, 2]),
    )
    fields = [
        NodalFieldData(
            "DISP",
            step,
            ["U1", "U2", "U3"],
            np.column_stack([ids, bending * step]),
            eigen_freq=10.0 * step,
            field_type=NodalFieldType.DISP,
        )
        for step in range(1, n_modes + 1)
    ]
    # A nodal stress per mode, as Sesam's reader gives: nodal, so not an element field, and not
    # something a mode-shape snapshot keeps.
    fields += [
        NodalFieldData(
            "NSTRESS",
            step,
            ["SXX", "SYY"],
            np.column_stack([ids, np.ones((6, 2)) * step]),
            eigen_freq=10.0 * step,
            field_type=NodalFieldType.UNKNOWN,
        )
        for step in range(1, n_modes + 1)
    ]
    return FEAResult(
        name=KEY,
        software=FEATypes.ABAQUS,
        results=fields,
        mesh=Mesh(elements=[block], nodes=FemNodes(coords=POINTS.copy(), identifiers=ids)),
    )


@pytest.fixture
def rendered(monkeypatch):
    """Stub the poster renderer; record which modes it was asked for."""
    from PIL import Image

    import ada.visit.rendering.fea_offscreen as offscreen

    calls = []

    def _render(case_dir, mode_index=0, **_kw):
        calls.append(mode_index)
        return Image.new("RGB", (4, 4))

    monkeypatch.setattr(offscreen, "render_fea_mode_from_bundle", _render)
    return calls


def test_snapshot_is_lean_and_referenced(tmp_path):
    ref = snapshot_fea_bundle(_eigen_result(), key=KEY, cache_dir=tmp_path)
    case_dir = tmp_path / KEY

    assert ref["dir"] == KEY
    assert ref["n_steps"] == 3
    names = {p.name for p in case_dir.iterdir()}
    assert {"fea.manifest.json", "fea.mesh.glb", "fea.DISP.bin"} <= names
    assert not [n for n in names if n.endswith(".png")]  # posters are re-rendered, not committed
    assert "fea.NSTRESS.bin" not in names  # the mode shapes, nothing else

    # Committed to a public repo: nothing in the manifest may name the machine that baked it.
    text = (case_dir / "fea.manifest.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text
    assert json.loads(text)["src"] == KEY


def test_json_snapshot_carries_the_reference_and_the_walk_ignores_the_bundle(tmp_path):
    case = FeaCaseResult(name=KEY, fem_format="abaqus", results=_eigen_result())
    case.fea_bundle = snapshot_fea_bundle(case.results, key=KEY, cache_dir=tmp_path)
    case.save_to_json(tmp_path / KEY)

    loaded = walk_cached_case_results(FeaCaseResult, tmp_path)
    assert [c.name for c in loaded] == [KEY]  # the bundle's manifest is not a case
    assert loaded[0].fea_bundle == case.fea_bundle

    # A case without raw data keeps the snapshot shape it had before bundles existed.
    FeaCaseResult(name="plain", fem_format="calculix").save_to_json(tmp_path / "plain")
    assert "fea_bundle" not in json.loads((tmp_path / "plain.json").read_text())


def test_restore_copies_into_assets_and_renders_every_mode(tmp_path, rendered):
    cache, assets_dir = tmp_path / ".cache", tmp_path / "_assets"
    snapshot_fea_bundle(_eigen_result(), key=KEY, cache_dir=cache)

    (restored,) = restore_fea_bundles(cache, assets_dir, prefix="cantilever_")

    assert restored.bundle_dir == assets_dir / KEY
    assert sorted(restored.poster_paths) == [0, 1, 2]
    assert restored.canonical_poster_path == assets_dir / KEY / "fea.mesh.png"
    assert (assets_dir / KEY / "fea.mesh.mode_3.png").is_file()
    assert restored.frequencies == pytest.approx([10.0, 20.0, 30.0])
    # The cache is read, never written.
    assert not list(cache.rglob("*.png"))

    # A second build finds the same manifest in place and re-renders nothing.
    rendered.clear()
    (again,) = restore_fea_bundles(cache, assets_dir)
    assert rendered == [] and sorted(again.poster_paths) == [0, 1, 2]


def test_restore_skips_fresh_keys_and_other_prefixes(tmp_path, rendered):
    cache, assets_dir = tmp_path / ".cache", tmp_path / "_assets"
    snapshot_fea_bundle(_eigen_result(), key=KEY, cache_dir=cache)

    assert restore_fea_bundles(cache, assets_dir, skip_keys={KEY}) == []
    assert restore_fea_bundles(cache, assets_dir, prefix="plate_") == []
    assert not assets_dir.exists()


def test_a_changed_snapshot_replaces_the_stale_copy(tmp_path, rendered):
    cache, assets_dir = tmp_path / ".cache", tmp_path / "_assets"
    snapshot_fea_bundle(_eigen_result(3), key=KEY, cache_dir=cache)
    restore_fea_bundles(cache, assets_dir)

    snapshot_fea_bundle(_eigen_result(2), key=KEY, cache_dir=cache)
    (restored,) = restore_fea_bundles(cache, assets_dir)

    assert sorted(restored.poster_paths) == [0, 1]
    assert not (assets_dir / KEY / "fea.mesh.mode_3.png").exists()

"""Tests for the diff viewer-utility: GLB element parsing + the 4 diff modes."""

from __future__ import annotations

import io
import pathlib

import ada
from ada.comms.rest.utilities import diff as diffmod


def _glb(*objects) -> bytes:
    a = ada.Assembly("A") / (ada.Part("p") / list(objects))
    buf = io.BytesIO()
    a.to_gltf(buf, merge_meshes=True)
    return buf.getvalue()


def _scene():
    return _glb(
        ada.Beam("b1", (0, 0, 0), (2, 0, 0), "IPE200"),
        ada.Beam("b2", (0, 1, 0), (2, 1, 0), "IPE200"),
        ada.Plate.from_3d_points("pl1", [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)], 0.01),
    )


def test_parse_elements_basic():
    elems = diffmod.parse_elements(_scene())
    assert set(elems) == {"b1", "b2", "pl1"}
    assert elems["b1"].etype == "Beam"
    assert elems["pl1"].etype == "Plate"
    # plate centroid ~ (1, 0.5, 0); area > 0
    c = elems["pl1"].centroid
    assert abs(c[0] - 1.0) < 0.2 and abs(c[1] - 0.5) < 0.2
    assert elems["pl1"].area > 0
    assert elems["b1"].guid  # guid carried through


def test_by_name_added_removed():
    scene = diffmod.parse_elements(_scene())
    # ref = scene minus b2, so b2 is "added" in scene; nothing removed
    ref = diffmod.parse_elements(
        _glb(
            ada.Beam("b1", (0, 0, 0), (2, 0, 0), "IPE200"),
            ada.Plate.from_3d_points("pl1", [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)], 0.01),
        )
    )
    res = diffmod._by_identity(scene, ref, lambda e: e.name)
    assert res["counts"]["added"] == 1  # b2
    assert res["counts"]["removed"] == 0
    assert res["counts"]["unchanged"] == 2


def test_by_property_detects_section_change():
    scene = diffmod.parse_elements(_scene())
    # ref: same names/centroids but b1 has a different section -> modified
    ref = diffmod.parse_elements(
        _glb(
            ada.Beam("b1", (0, 0, 0), (2, 0, 0), "HEB200"),
            ada.Beam("b2", (0, 1, 0), (2, 1, 0), "IPE200"),
            ada.Plate.from_3d_points("pl1", [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)], 0.01),
        )
    )
    res = diffmod._by_identity(scene, ref, lambda e: e.name, compare_props=True)
    assert res["counts"]["modified"] >= 1


def test_by_coverage_counts_fragmentation():
    # scene has a plate split into 2 halves; ref has it as 1 -> count delta in the cell
    scene = diffmod.parse_elements(
        _glb(
            ada.Plate.from_3d_points("h1", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 0.01),
            ada.Plate.from_3d_points("h2", [(1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0)], 0.01),
        )
    )
    ref = diffmod.parse_elements(
        _glb(ada.Plate.from_3d_points("p", [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)], 0.01))
    )
    res = diffmod._by_coverage(scene, ref, grid_size=10.0)  # one big cell
    assert res["summary"]["net_element_delta"] == 1  # 2 vs 1
    assert res["summary"]["cells_changed"] == 1


class _FakeStorage:
    def __init__(self, ref_glb: bytes):
        self._ref = ref_glb
        self.put: dict[str, bytes] = {}

    def list_keys(self, prefix=""):
        return ["versions/main/abc123/model.glb"]

    def fetch_to_path(self, key, dest):
        pathlib.Path(dest).write_bytes(self._ref)
        return dest

    def put_bytes(self, key, data):
        self.put[key] = data


def test_resolve_ref_glb_full_key_and_branch():
    ref_glb = _glb(ada.Beam("b1", (0, 0, 0), (1, 0, 0), "IPE200"))

    class _S:
        def list_keys(self, prefix=""):
            return ["versions/main/abc123/model.glb"]

        def fetch_to_path(self, key, dest):
            pathlib.Path(dest).write_bytes(ref_glb)
            return dest

    s = _S()
    # full blob key -> used verbatim
    assert diffmod.resolve_ref_glb(s, "versions/main/abc123/model.glb")[:4] == b"glTF"
    # branch -> resolved
    assert diffmod.resolve_ref_glb(s, "main")[:4] == b"glTF"
    # commit sha -> resolved
    assert diffmod.resolve_ref_glb(s, "abc123")[:4] == b"glTF"
    import pytest

    with pytest.raises(ValueError):
        diffmod.resolve_ref_glb(s, "versions/main/zzz/missing.glb")


def test_diff_handler_end_to_end(tmp_path):
    scene_glb = _scene()
    ref_glb = _glb(  # ref missing b2 (=> removed overlay), pl1 unchanged, b1 unchanged
        ada.Beam("b1", (0, 0, 0), (2, 0, 0), "IPE200"),
        ada.Plate.from_3d_points("pl1", [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)], 0.01),
        ada.Beam("bx", (0, 5, 0), (2, 5, 0), "IPE200"),  # only in ref => removed
    )
    sp = tmp_path / "scene.glb"
    sp.write_bytes(scene_glb)
    storage = _FakeStorage(ref_glb)

    payload = diffmod.diff(
        str(sp),
        storage=storage,
        scope=None,
        on_progress=lambda *_: None,
        compare_ref="main",
        diff_type="byName",
    )
    assert payload["ops"][0]["op"] == "color_elements"
    assert payload["summary"]["removed"] == 1  # bx
    # removed overlay was built + uploaded, and referenced by an op
    overlay_ops = [o for o in payload["ops"] if o["op"] == "add_overlay_geometry"]
    assert len(overlay_ops) == 1
    assert overlay_ops[0]["blob_key"] in storage.put


class _MockStorage:
    """Scope-bound storage stub: keys -> GLB bytes (mirrors _SyncStorageFacade)."""

    def __init__(self, files: dict):
        self.files = files

    def list_keys(self, prefix: str = "") -> list:
        return [k for k in self.files if k.startswith(prefix)]

    def fetch_to_path(self, key: str, dest):
        if key not in self.files:
            raise FileNotFoundError(key)
        with open(dest, "wb") as fh:
            fh.write(self.files[key])
        return dest


def test_resolve_ref_glb_accepts_arbitrary_file_key():
    # Comparing two arbitrary uploaded files: a direct .glb key (not under
    # versions/) is fetched verbatim from the scope.
    glb = _scene()
    other = _glb(ada.Beam("x", (0, 0, 0), (1, 0, 0), "IPE200"))
    st = _MockStorage({"debug/other.glb": other, "versions/main/abc1234/m.glb": glb})

    assert diffmod.resolve_ref_glb(st, "debug/other.glb") == other
    # versions/ build keys still resolve as before
    assert diffmod.resolve_ref_glb(st, "versions/main/abc1234/m.glb") == glb


def test_resolve_ref_glb_missing_arbitrary_key_errors_clearly():
    import pytest

    st = _MockStorage({"debug/present.glb": _scene()})
    with pytest.raises(ValueError, match="compare file not found"):
        diffmod.resolve_ref_glb(st, "debug/missing.glb")

"""ada.assoc.fingerprints: per-element fingerprints of a GLB, by GLB id and by element key."""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

from ada.assoc.fingerprints import (  # noqa: E402
    element_fingerprints,
    keyed_fingerprints,
)
from ada.assoc.resolve import glb_json  # noqa: E402
from ada.geom_compare import glb  # noqa: E402

_CUBE_V = [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1]
_CUBE_T = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7]


def _cube(at=0.0):
    return [v + (at if i % 3 == 0 else 0.0) for i, v in enumerate(_CUBE_V)], list(_CUBE_T)


def _glb(tmp_path, meshes, name="m.glb") -> bytes:
    glb.write(meshes, tmp_path / name)
    return (tmp_path / name).read_bytes()


def test_each_element_has_a_fingerprint_that_moves_with_its_geometry(tmp_path):
    one = _glb(tmp_path, {"A": _cube(), "B": _cube(3.0)}, "one.glb")
    two = _glb(tmp_path, {"A": _cube(), "B": _cube(3.5)}, "two.glb")
    fa, fb = element_fingerprints(one), element_fingerprints(two)
    assert len(fa) == 2 and set(fa) == set(fb)
    names = {eid: n for eid, (n, _) in glb.merged_hierarchy(glb.parse_glb(one)[0]).by_id.items()}
    by_name_a = {names[e]: f for e, f in fa.items()}
    by_name_b = {names[e]: f for e, f in fb.items()}
    assert by_name_a["A"] == by_name_b["A"] and by_name_a["B"] != by_name_b["B"]

    keyed = keyed_fingerprints({"kind": "file", "source_key": "m.stp"}, one)
    assert set(keyed) == {"file:m.stp#p:A", "file:m.stp#p:B"}
    assert glb_json(one)["scenes"]

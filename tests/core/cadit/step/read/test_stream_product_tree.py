"""from_step streaming import: optional product-tree reconstruction (nested Parts).

The streaming reader yields one Geometry per solid carrying its assembly path
(``instance_paths`` — root-first ``(rep_id, product_name)`` levels, last level = the solid
itself). ``read_step_file(product_tree=True)`` turns those paths into nested ``Part``s
(same-name siblings merged); the default (``product_tree=False``) keeps the flat list.
"""

import ada
from ada.geom import Geometry


def _box_geom():
    # A real ada.geom solid to wrap in each Shape.
    return ada.PrimBox("b", (0, 0, 0), (1, 1, 1)).solid_geom().geometry


def _patch_stream(monkeypatch, items):
    geom = _box_geom()

    def fake_stream(*a, **k):
        for name, ip in items:
            yield Geometry(name, geom, instance_paths=ip)

    import ada.cadit.step.read.stream_reader as sr

    monkeypatch.setattr(sr, "stream_read_step", fake_stream)


def test_product_tree_builds_nested_parts(monkeypatch):
    _patch_stream(
        monkeypatch,
        [
            ("p1", [((1, "asmA"), (2, "sub1"), (3, "p1"))]),
            ("p2", [((1, "asmA"), (2, "sub1"), (4, "p2"))]),  # same sub1 as p1 (merge)
            ("p3", [((1, "asmA"), (5, "sub2"), (6, "p3"))]),
            ("flat", None),  # no hierarchy → stays directly under root
        ],
    )
    a = ada.Assembly("root")
    a.read_step_file("dummy.step", reader="stream", product_tree=True)

    asm_a = a.parts["asmA"]
    assert set(asm_a.parts.keys()) == {"sub1", "sub2"}
    assert {s.name for s in asm_a.parts["sub1"].shapes} == {"p1", "p2"}
    assert {s.name for s in asm_a.parts["sub2"].shapes} == {"p3"}
    assert "flat" in {s.name for s in a.shapes}  # the no-path solid stayed flat


def test_flat_default_no_hierarchy(monkeypatch):
    _patch_stream(
        monkeypatch,
        [(n, [((1, "asmA"), (2, n))]) for n in ("p1", "p2", "p3")],
    )
    a = ada.Assembly("root")
    a.read_step_file("dummy.step", reader="stream")  # product_tree defaults False

    assert {s.name for s in a.shapes} == {"p1", "p2", "p3"}
    assert "asmA" not in a.parts  # tree NOT reconstructed by default

"""Dimensioned SVG drawings of a small portal frame with a tank on a skid (ada.drawings).

    pixi run -e tests python examples/drawings_portal_frame.py   # writes temp/drawings/*.svg
"""

from __future__ import annotations

import pathlib

import ada
from ada.drawings import (
    Caption,
    Drawing,
    Leader,
    LinearDimension,
    Polyline,
    RadiusDimension,
    ScaleBar,
    SectionPlane,
    SectionSymbol,
    TitleBlock,
    View,
)
from ada.drawings.anchors import bbox_point, beam_end

OUT = pathlib.Path(__file__).resolve().parents[1] / "temp" / "drawings"


def build_model() -> tuple[ada.Part, dict]:
    span, height, depth = 8.0, 5.0, 4.0
    objs: dict = {}
    for k, y in enumerate((0.0, depth)):
        objs[f"col_l{k}"] = ada.Beam(f"col_l{k}", (0, y, 0), (0, y, height), "HEB300")
        objs[f"col_r{k}"] = ada.Beam(f"col_r{k}", (span, y, 0), (span, y, height), "HEB300")
        objs[f"rafter{k}"] = ada.Beam(f"rafter{k}", (0, y, height), (span, y, height), "IPE400")
    objs["tie_l"] = ada.Beam("tie_l", (0, 0, height), (0, depth, height), "OD219x8")
    objs["tie_r"] = ada.Beam("tie_r", (span, 0, height), (span, depth, height), "OD219x8")
    objs["skid"] = ada.PrimBox("skid", (2.5, 0.8, 0.0), (5.5, 3.2, 0.3))
    objs["tank"] = ada.PrimCyl("tank", (4.0, 2.0, 0.3), (4.0, 2.0, 3.3), 1.1)
    objs["dome"] = ada.PrimSphere("dome", (4.0, 2.0, 3.3), 1.1)
    p = ada.Part("PortalFrame") / list(objs.values())
    return p, objs


def front(p, o) -> Drawing:
    d = Drawing("Portal frame - front elevation", view="front", scale=50)
    d.add(p)
    d.add(Polyline([(0.0, -0.1, 5.0), (4.0, -0.1, 7.0), (8.0, -0.1, 5.0)], "guy", "wire"), group="guy")
    d.style("guy", color="#b0602a", width=0.35)
    d.style("shape", color="#333333")
    d.annotate(
        LinearDimension(beam_end(o["col_l0"], "start"), beam_end(o["col_r0"], "start"), "horizontal", offset=-12),
        LinearDimension(beam_end(o["col_r0"], "start"), beam_end(o["col_r0"], "end"), "vertical", offset=12),
        LinearDimension(bbox_point(o["skid"], 0, 0.5, 0), bbox_point(o["skid"], 1, 0.5, 0), "horizontal", offset=-6),
        LinearDimension((4.0, 2.0, 0.0), (4.0, 2.0, 4.4), "vertical", offset=-30, text="4.40", prefix="top of tank "),
        Leader(beam_end(o["rafter0"], "end"), "IPE400", offset=(10, 8)),
        Leader(bbox_point(o["tank"], 1.0, 0.5, 0.5), "tank Ø2.2", offset=(14, 4)),
        Leader((4.0, -0.1, 7.0), "guy wire", offset=(12, 6)),
        SectionSymbol((4.0, -1.0, 2.0), (4.0, 5.0, 2.0), "A"),
        Caption("FRONT ELEVATION"),
        ScaleBar(),
    )
    return d


def section_a(p, o) -> Drawing:
    view = View(direction=(0, 0, -1), up=(0, 1, 0), name="section A-A")
    d = Drawing("Portal frame - section A-A", view=view, scale=50, section=SectionPlane((0, 0, 2.0), (0, 0, -1)))
    d.add(p)
    d.style("shape", cut_fill="hatch")
    d.annotate(
        RadiusDimension((4.0, 2.0, 2.0), 1.1, "diameter", angle=30),
        LinearDimension((0.0, 0.0, 2.0), (0.0, 4.0, 2.0), "vertical", offset=-12),
        Caption("SECTION A-A"),
        TitleBlock("Portal frame with tank", subtitle="Section A-A at z = 2.0 m", number="EX-001", revision="A"),
    )
    return d


def iso(p) -> Drawing:
    d = Drawing("Portal frame - isometric", view="iso", scale=75, show_hidden=True)
    d.add(p)
    d.annotate(Caption("ISOMETRIC", show_scale=False))
    return d


def main():
    p, o = build_model()
    for name, d in (("front", front(p, o)), ("section_a", section_a(p, o)), ("iso", iso(p))):
        path = OUT / f"portal_{name}.svg"
        d.to_svg(path)
        print(path, "skipped:", d.skipped)


if __name__ == "__main__":
    main()

"""Standalone SVG output: paper millimetres, CSS classes per line type, markers for arrows.

The class vocabulary follows the convention of drawing tools that style SVG with CSS (Bonsai's
drawing sheets among them): ``cut`` heavy, ``projection`` light, ``hidden`` dashed,
``centerline`` dash-dot, ``annotation``-family classes for dimensions, leaders and text.
Every linework element also carries ``g-<group>`` so a drawing can colour a group of objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

import numpy as np

from ada.drawings.annotations import CHAR_WIDTH, PathPrim, TextPrim

DEFAULT_CSS = """
* { stroke-linecap: round; stroke-linejoin: round; }
text, tspan { font-family: 'DejaVu Sans', 'Liberation Sans', Arial, Helvetica, sans-serif; fill: #000; stroke: none; }
.sheet { fill: #fff; stroke: none; }
.cut { fill: none; stroke: #000; stroke-width: 0.5; }
.cut-fill { fill: #d4d4d4; stroke: none; fill-rule: evenodd; }
.projection { fill: none; stroke: #000; stroke-width: 0.25; }
.hidden { fill: none; stroke: #000; stroke-width: 0.18; stroke-dasharray: 1.5, 1; }
.centerline { fill: none; stroke: #000; stroke-width: 0.18; stroke-dasharray: 6, 1.2, 1, 1.2; }
.wire { fill: none; stroke: #000; stroke-width: 0.25; }
.dimension { fill: none; stroke: #1f4e9c; stroke-width: 0.18; }
.dimension.extension { stroke-width: 0.13; }
.dimension-text { fill: #1f4e9c; }
.leader { fill: none; stroke: #000; stroke-width: 0.18; }
.section-line { fill: none; stroke: #000; stroke-width: 0.35; stroke-dasharray: 8, 1.5, 1.5, 1.5; }
.section-arrow { fill: none; stroke: #000; stroke-width: 0.35; }
.section-label { font-weight: bold; }
.text-bg { fill: #fff; fill-opacity: 0.85; stroke: none; }
.marker-fill { fill: #000; stroke: none; }
.dimension-marker { fill: #1f4e9c; stroke: none; }
.title-block, .sheet-frame { fill: none; stroke: #000; stroke-width: 0.35; }
.title-block-rule { fill: none; stroke: #000; stroke-width: 0.18; }
.scale-bar { fill: #fff; stroke: #000; stroke-width: 0.18; }
.scale-bar-fill { fill: #000; stroke: #000; stroke-width: 0.18; }
.caption { font-weight: bold; }
"""

_MARKERS = """
<marker id="dim-end" markerUnits="userSpaceOnUse" viewBox="-3 -3 6 6" markerWidth="6" markerHeight="6" orient="auto" overflow="visible"><path class="dimension-marker" d="M0,0 L-2.5,-0.55 L-2.5,0.55 Z"/></marker>
<marker id="dim-start" markerUnits="userSpaceOnUse" viewBox="-3 -3 6 6" markerWidth="6" markerHeight="6" orient="auto" overflow="visible"><path class="dimension-marker" d="M0,0 L2.5,-0.55 L2.5,0.55 Z"/></marker>
<marker id="leader-arrow" markerUnits="userSpaceOnUse" viewBox="-3 -3 6 6" markerWidth="6" markerHeight="6" orient="auto" overflow="visible"><path class="marker-fill" d="M0,0 L2.5,-0.6 L2.5,0.6 Z"/></marker>
<marker id="leader-dot" markerUnits="userSpaceOnUse" viewBox="-3 -3 6 6" markerWidth="6" markerHeight="6" orient="auto" overflow="visible"><circle class="marker-fill" cx="0" cy="0" r="0.6"/></marker>
<marker id="section-head" markerUnits="userSpaceOnUse" viewBox="-4 -4 8 8" markerWidth="8" markerHeight="8" orient="auto" overflow="visible"><path class="marker-fill" d="M0,0 L-3.2,-1.2 L-3.2,1.2 Z"/></marker>
<pattern id="hatch" patternUnits="userSpaceOnUse" width="1.2" height="1.2" patternTransform="rotate(45)"><rect width="1.2" height="1.2" fill="#fff"/><line x1="0" y1="0" x2="0" y2="1.2" stroke="#000" stroke-width="0.15"/></pattern>
"""


def css_ident(name: str) -> str:
    """A group name as a CSS class (``g-<name>``, non-identifier characters as ``-``)."""
    return "g-" + re.sub(r"[^A-Za-z0-9_-]", "-", name)


@dataclass
class GroupStyle:
    color: str | None = None
    width: float | None = None
    cut_fill: str | None = None  # a colour, or "hatch"
    dash: str | None = None

    def css(self, group: str) -> str:
        g = "." + css_ident(group)
        rules = []
        if self.color:
            rules.append(f"{g}.cut, {g}.projection, {g}.hidden, {g}.centerline, {g}.wire {{ stroke: {self.color}; }}")
        if self.width:
            rules.append(f"{g}.projection, {g}.wire {{ stroke-width: {self.width}; }}")
            rules.append(f"{g}.cut {{ stroke-width: {self.width * 1.6:.3f}; }}")
        if self.cut_fill:
            fill = "url(#hatch)" if self.cut_fill == "hatch" else self.cut_fill
            rules.append(f"{g}.cut-fill {{ fill: {fill}; }}")
        if self.dash:
            rules.append(f"{g}.projection, {g}.wire {{ stroke-dasharray: {self.dash}; }}")
        return "\n".join(rules)


@dataclass
class SheetContent:
    """Everything on a sheet in paper millimetres (y down), already translated onto the sheet."""

    width: float
    height: float
    title: str
    strokes: list[tuple[np.ndarray, str, str]] = field(default_factory=list)  # points, kind, group
    regions: list[tuple[list[np.ndarray], str]] = field(default_factory=list)  # loops, group
    prims: list = field(default_factory=list)  # PathPrim / TextPrim / raw svg str
    css: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pts(a: np.ndarray) -> str:
    return " ".join(f"{_f(x)},{_f(y)}" for x, y in a)


def _path_d(loops: list[np.ndarray]) -> str:
    return " ".join("M" + " L".join(f"{_f(x)},{_f(y)}" for x, y in lp) + " Z" for lp in loops if len(lp) >= 3)


def text_svg(t: TextPrim) -> str:
    lines = t.text.split("\n")
    w = max(len(s) for s in lines) * CHAR_WIDTH * t.size
    h = t.size * (1.25 * len(lines) - 0.25)
    rot = f' transform="rotate({_f(t.rotation)} {_f(t.x)} {_f(t.y)})"' if abs(t.rotation) > 1e-9 else ""
    out = []
    if t.background:
        x0 = {"start": t.x, "middle": t.x - 0.5 * w, "end": t.x - w}[t.anchor]
        out.append(
            f'<rect class="text-bg" x="{_f(x0 - 0.5)}" y="{_f(t.y - 0.8 * t.size - 0.4)}" width="{_f(w + 1.0)}" '
            f'height="{_f(h + 0.8)}"{rot}/>'
        )
    body = escape(lines[0])
    for ln in lines[1:]:
        body += f'<tspan x="{_f(t.x)}" dy="{_f(1.25 * t.size)}">{escape(ln)}</tspan>'
    out.append(
        f'<text class="{t.cls}" x="{_f(t.x)}" y="{_f(t.y)}" font-size="{_f(t.size)}" text-anchor="{t.anchor}"{rot}>'
        f"{body}</text>"
    )
    return "".join(out)


def path_svg(p: PathPrim) -> str:
    attrs = ""
    if p.marker_start:
        attrs += f' marker-start="url(#{p.marker_start})"'
    if p.marker_end:
        attrs += f' marker-end="url(#{p.marker_end})"'
    tag = "polygon" if p.closed else "polyline"
    return f'<{tag} class="{p.cls}" points="{_pts(p.points)}"{attrs}/>'


def write_svg(sheet: SheetContent) -> str:
    w, h = sheet.width, sheet.height
    meta = "".join(f' data-{k}="{escape(v)}"' for k, v in sheet.metadata.items())
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{_f(w)}mm" height="{_f(h)}mm" '
        f'viewBox="0 0 {_f(w)} {_f(h)}"{meta}>',
        f"<title>{escape(sheet.title)}</title>",
        f"<defs>{_MARKERS}<style>{DEFAULT_CSS}{sheet.css}</style></defs>",
        f'<rect class="sheet" x="0" y="0" width="{_f(w)}" height="{_f(h)}"/>',
    ]
    if sheet.regions:
        out.append('<g id="cut-fill">')
        for loops, group in sheet.regions:
            d = _path_d(loops)
            if d:
                out.append(f'<path class="cut-fill {css_ident(group)}" d="{d}"/>')
        out.append("</g>")
    for layer in ("hidden", "centerline", "projection", "wire", "cut"):
        rows = [(p, g) for p, k, g in sheet.strokes if k == layer]
        if not rows:
            continue
        out.append(f'<g id="{layer}">')
        for pts, group in rows:
            out.append(f'<polyline class="{layer} {css_ident(group)}" points="{_pts(pts)}"/>')
        out.append("</g>")
    out.append('<g id="annotation">')
    for pr in sheet.prims:
        if isinstance(pr, TextPrim):
            continue
        out.append(pr if isinstance(pr, str) else path_svg(pr))
    for pr in sheet.prims:  # text last, on top of every line
        if isinstance(pr, TextPrim):
            out.append(text_svg(pr))
    out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"

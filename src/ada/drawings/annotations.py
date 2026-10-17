"""Annotations: dimensions, labels, leaders, section symbols, title block, caption, scale bar.

Annotations are placed in **model coordinates** (3D points, usually taken from the objects with
the helpers in :mod:`ada.drawings.anchors`) and laid out on paper when the drawing is rendered,
so they follow the view and the scale. Offsets and sizes that only make sense on paper (how far
a dimension line sits from what it measures, the text height) are in paper millimetres.

Each annotation turns into SVG primitives (:class:`PathPrim`, :class:`TextPrim`) through
``layout(ctx)``; :class:`LayoutContext` carries the projector, the number format and the boxes
of text already placed, which dimensions and leaders use to move clear of each other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np

from ada.drawings.view import Projector, Units, mm_per_unit

PointLike = Sequence[float] | np.ndarray
TextSize = Literal["small", "regular", "large", "header", "title"]
TEXT_SIZES: dict[str, float] = {"small": 1.8, "regular": 2.5, "large": 3.5, "header": 5.0, "title": 7.0}
CHAR_WIDTH = 0.6  # average glyph advance / text height for a sans-serif face
ARROW = 2.5  # mm, arrow head length (keep in sync with the SVG markers)


# --------------------------------------------------------------------------------------------
# primitives and layout context
# --------------------------------------------------------------------------------------------


@dataclass
class PathPrim:
    points: np.ndarray  # paper mm, y down
    cls: str
    closed: bool = False
    marker_start: str | None = None
    marker_end: str | None = None


@dataclass
class TextPrim:
    x: float
    y: float
    text: str
    size: float
    cls: str = "text"
    anchor: Literal["start", "middle", "end"] = "middle"
    rotation: float = 0.0  # degrees, clockwise on paper (SVG)
    background: bool = True


@dataclass
class DimensionFormat:
    """How measured values print: in ``unit`` with ``precision`` decimals (``show_unit`` adds it)."""

    unit: Units = "m"
    precision: int = 2
    show_unit: bool = False

    def format(self, value_model: float, model_units: Units) -> str:
        v = value_model * mm_per_unit(model_units) / mm_per_unit(self.unit)
        s = f"{v:.{self.precision}f}"
        return f"{s} {self.unit}" if self.show_unit else s


def text_box(t: TextPrim) -> tuple[np.ndarray, np.ndarray]:
    """Axis-aligned paper box of a text primitive (approximate glyph metrics)."""
    lines = t.text.split("\n")
    w = max(len(s) for s in lines) * CHAR_WIDTH * t.size
    h = t.size * (1.25 * len(lines) - 0.25)
    x0 = {"start": 0.0, "middle": -0.5 * w, "end": -w}[t.anchor]
    # baseline at y: glyphs from y - 0.8 h (cap height) down to y + 0.2 h, further lines below
    corners = np.array(
        [[x0, -0.8 * t.size], [x0 + w, -0.8 * t.size], [x0 + w, h - 0.8 * t.size], [x0, h - 0.8 * t.size]]
    )
    a = math.radians(t.rotation)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    c = corners @ rot.T + np.array([t.x, t.y])
    return c.min(axis=0), c.max(axis=0)


@dataclass
class LayoutContext:
    projector: Projector
    fmt: DimensionFormat
    content: tuple[np.ndarray, np.ndarray]  # paper bbox of the linework
    boxes: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list)
    dim_offset: float = 8.0
    tier: float = 7.0

    def p(self, pt: PointLike) -> np.ndarray:
        return self.projector.point(np.asarray(pt, float))

    def to_model(self, paper_len: float) -> float:
        return paper_len / self.projector.k

    def collides(self, box, pad: float = 0.6) -> bool:
        lo, hi = box
        for blo, bhi in self.boxes:
            if lo[0] - pad < bhi[0] and hi[0] + pad > blo[0] and lo[1] - pad < bhi[1] and hi[1] + pad > blo[1]:
                return True
        return False

    def reserve(self, prims: list) -> None:
        for pr in prims:
            if isinstance(pr, TextPrim):
                self.boxes.append(text_box(pr))

    @property
    def center(self) -> np.ndarray:
        return 0.5 * (self.content[0] + self.content[1])


def _readable(u: np.ndarray) -> tuple[np.ndarray, float]:
    """Direction flipped so text along it reads left-to-right / bottom-to-top; its SVG angle."""
    ang = math.degrees(math.atan2(u[1], u[0]))
    if ang > 90.0 + 1e-6 or ang <= -90.0 + 1e-6:
        u = -u
        ang = math.degrees(math.atan2(u[1], u[0]))
    return u, ang


def _text_up(ang: float) -> np.ndarray:
    a = math.radians(ang)
    return np.array([math.sin(a), -math.cos(a)])


# --------------------------------------------------------------------------------------------
# dimensions
# --------------------------------------------------------------------------------------------


@dataclass
class LinearDimension:
    """Distance between two model points, measured on the sheet.

    ``orientation``: ``"horizontal"`` / ``"vertical"`` measure along the sheet axes,
    ``"aligned"`` along p1 -> p2. ``offset`` (paper mm) places the dimension line: positive is
    up (horizontal), right (vertical) or left of p1 -> p2 (aligned); ``None`` picks the side away
    from the drawing's centre at the default distance; ``"outside"`` puts the line just beyond
    the drawing's extent on that side. A dimension that would hit text already placed steps out
    one tier at a time. ``text`` replaces the measured value; ``prefix``/``suffix`` wrap it.
    A dimension too short for its text draws the arrows from outside and puts the text past
    the end that reads last (``short_text="after"``) or first (``"before"``).
    """

    p1: PointLike
    p2: PointLike
    orientation: Literal["horizontal", "vertical", "aligned"] = "aligned"
    offset: float | Literal["outside"] | None = None
    text: str | None = None
    prefix: str = ""
    suffix: str = ""
    size: TextSize = "regular"
    short_text: Literal["after", "before"] = "after"

    def layout(self, ctx: LayoutContext) -> list:
        a0, b0 = ctx.p(self.p1), ctx.p(self.p2)
        if self.orientation == "horizontal":
            u, n = np.array([1.0, 0.0]), np.array([0.0, -1.0])
        elif self.orientation == "vertical":
            u, n = np.array([0.0, -1.0]), np.array([1.0, 0.0])
        else:
            d = b0 - a0
            ln = float(np.linalg.norm(d))
            if ln < 1e-9:
                return []
            u = d / ln
            n = np.array([u[1], -u[0]])
        length_paper = abs(float((b0 - a0) @ u))
        if length_paper < 1e-9:
            return []
        s1, s2 = float(a0 @ n), float(b0 @ n)
        if isinstance(self.offset, (int, float)):
            sign = 1.0 if self.offset >= 0 else -1.0
            dist = abs(float(self.offset))
        else:
            mid = 0.5 * (a0 + b0)
            sign = 1.0 if float((mid - ctx.center) @ n) >= 0 else -1.0
            dist = ctx.dim_offset
        nn = n * sign
        base = max(s1 * sign, s2 * sign)
        if self.offset == "outside":
            lo, hi = ctx.content
            corners = np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]])
            base = max(base, float((corners @ nn).max()))
            dist = 6.0
        value = self.text
        if value is None:
            value = ctx.fmt.format(ctx.to_model(length_paper), ctx.projector.units)
        label = f"{self.prefix}{value}{self.suffix}"
        h = TEXT_SIZES[self.size]
        prims = []
        for _ in range(8):
            prims = self._build(a0, b0, u, nn, base + dist, sign, label, h)
            if not any(isinstance(p, TextPrim) and ctx.collides(text_box(p)) for p in prims):
                break
            dist += ctx.tier
        ctx.reserve(prims)
        return prims

    def _build(self, a0, b0, u, nn, line_s, sign, label, h) -> list:
        a = a0 + (line_s - float(a0 @ nn)) * nn
        b = b0 + (line_s - float(b0 @ nn)) * nn
        prims: list = []
        for p, q in ((a0, a), (b0, b)):
            if np.linalg.norm(q - p) > 1.0:
                prims.append(PathPrim(np.array([p + nn * 1.0, q + nn * 1.5]), "dimension extension"))
        ur, ang = _readable(u)
        up = _text_up(ang)
        span = float(np.linalg.norm(b - a))
        tw = len(label) * CHAR_WIDTH * h
        if span >= tw + 2 * ARROW + 2.0:
            prims.append(PathPrim(np.array([a, b]), "dimension", marker_start="dim-start", marker_end="dim-end"))
            pos = 0.5 * (a + b) + up * 1.0
            prims.append(TextPrim(pos[0], pos[1], label, h, "text dimension-text", "middle", ang))
        else:  # too short: arrows from outside, text beyond the far end
            d = (b - a) / max(span, 1e-9)
            prims.append(PathPrim(np.array([a - d * (ARROW + 3.0), a]), "dimension", marker_end="dim-end"))
            prims.append(PathPrim(np.array([b + d * (ARROW + 3.0), b]), "dimension", marker_end="dim-end"))
            prims.append(PathPrim(np.array([a, b]), "dimension"))
            last, first = (b, a) if float((b - a) @ ur) >= 0 else (a, b)
            if self.short_text == "after":
                pos = last + ur * (ARROW + 4.0 + 0.5 * tw) + up * 1.0
            else:
                pos = first - ur * (ARROW + 4.0 + 0.5 * tw) + up * 1.0
            prims.append(TextPrim(pos[0], pos[1], label, h, "text dimension-text", "middle", ang))
        return prims


@dataclass
class RadiusDimension:
    """Radius or diameter of a circle seen face-on: ``center`` in model coordinates, ``radius``
    in model units. ``angle`` (degrees, counter-clockwise from sheet right) is where the
    dimension crosses the circle."""

    center: PointLike
    radius: float
    kind: Literal["radius", "diameter"] = "radius"
    angle: float = 45.0
    text: str | None = None
    size: TextSize = "regular"

    def layout(self, ctx: LayoutContext) -> list:
        c = ctx.p(self.center)
        r = self.radius * ctx.projector.k
        d = np.array([math.cos(math.radians(self.angle)), -math.sin(math.radians(self.angle))])
        h = TEXT_SIZES[self.size]
        value = ctx.fmt.format(self.radius * (2.0 if self.kind == "diameter" else 1.0), ctx.projector.units)
        label = self.text or (f"Ø{value}" if self.kind == "diameter" else f"R{value}")
        tw = len(label) * CHAR_WIDTH * h
        prims: list = []
        if self.kind == "diameter" and 2 * r >= tw + 2 * ARROW + 2.0:
            a, b = c - d * r, c + d * r
            prims.append(PathPrim(np.array([a, b]), "dimension", marker_start="dim-start", marker_end="dim-end"))
            ur, ang = _readable(d)
            pos = c + ur * (0.5 * r) + _text_up(ang) * 1.0  # half-way out: clear of what sits at the centre
            prims.append(TextPrim(pos[0], pos[1], label, h, "text dimension-text", "middle", ang))
        else:
            rim = c + d * r
            start = c - d * r if self.kind == "diameter" else c
            prims.append(PathPrim(np.array([start, rim]), "dimension", marker_end="dim-end"))
            out = rim + d * 6.0
            side = 1.0 if d[0] >= 0 else -1.0
            land = out + np.array([side * 3.0, 0.0])
            prims.append(PathPrim(np.array([rim, out, land]), "dimension"))
            prims.append(
                TextPrim(
                    land[0] + side * 1.0,
                    land[1] + 0.35 * h,
                    label,
                    h,
                    "text dimension-text",
                    "start" if side > 0 else "end",
                )
            )
        ctx.reserve(prims)
        return prims


@dataclass
class AngleDimension:
    """Angle at ``vertex`` between the directions to ``p1`` and ``p2`` (as seen on the sheet).
    ``radius`` is the arc radius in paper mm; ``arms`` draws both directions out from the vertex
    (for an angle between lines the drawing does not show, e.g. an axis and the horizontal)."""

    vertex: PointLike
    p1: PointLike
    p2: PointLike
    radius: float = 12.0
    text: str | None = None
    precision: int = 0
    size: TextSize = "regular"
    arms: bool = False

    def layout(self, ctx: LayoutContext) -> list:
        v, a, b = ctx.p(self.vertex), ctx.p(self.p1), ctx.p(self.p2)
        # math angles (counter-clockwise, y up)
        t1 = math.atan2(-(a - v)[1], (a - v)[0])
        t2 = math.atan2(-(b - v)[1], (b - v)[0])
        sweep = (t2 - t1 + math.pi) % (2 * math.pi) - math.pi
        ts = np.linspace(t1, t1 + sweep, max(8, int(abs(sweep) * 24)))
        arc = np.stack([v[0] + self.radius * np.cos(ts), v[1] - self.radius * np.sin(ts)], axis=1)
        prims: list = [PathPrim(arc, "dimension", marker_start="dim-start", marker_end="dim-end")]
        for p, t in ((a, t1), (b, t1 + sweep)):
            e = v + np.array([math.cos(t), -math.sin(t)]) * (self.radius + 1.5)
            if self.arms:
                prims.append(PathPrim(np.array([v, e]), "dimension extension"))
            elif np.linalg.norm(p - v) < self.radius:
                prims.append(PathPrim(np.array([p, e]), "dimension extension"))
        h = TEXT_SIZES[self.size]
        tm = t1 + 0.5 * sweep
        label = self.text or f"{abs(math.degrees(sweep)):.{self.precision}f}°"
        pos = v + np.array([math.cos(tm), -math.sin(tm)]) * (self.radius + 1.5 + 0.5 * h)
        anchor = "start" if math.cos(tm) > 0.3 else ("end" if math.cos(tm) < -0.3 else "middle")
        prims.append(TextPrim(pos[0], pos[1] + 0.35 * h, label, h, "text dimension-text", anchor))
        ctx.reserve(prims)
        return prims


# --------------------------------------------------------------------------------------------
# text, leaders, section symbols
# --------------------------------------------------------------------------------------------


@dataclass
class Text:
    """Free text at a model point; ``offset`` (paper mm, +y up) shifts it on the sheet."""

    position: PointLike
    text: str
    size: TextSize = "regular"
    align: Literal["start", "middle", "end"] = "middle"
    rotation: float = 0.0
    offset: tuple[float, float] = (0.0, 0.0)
    background: bool = True

    def layout(self, ctx: LayoutContext) -> list:
        p = ctx.p(self.position) + np.array([self.offset[0], -self.offset[1]])
        h = TEXT_SIZES[self.size]
        prims = [TextPrim(p[0], p[1], self.text, h, f"text {self.size}", self.align, -self.rotation, self.background)]
        ctx.reserve(prims)
        return prims


@dataclass
class Leader:
    """A callout: a line from ``target`` (model point) to a text placed ``offset`` away (paper mm,
    +y up), ending in a short horizontal landing. If the text would overlap text already placed,
    the elbow moves further out along ``offset``."""

    target: PointLike
    text: str
    offset: tuple[float, float] = (10.0, 10.0)
    arrow: Literal["arrow", "dot", "none"] = "arrow"
    size: TextSize = "regular"

    def layout(self, ctx: LayoutContext) -> list:
        t = ctx.p(self.target)
        off = np.array([self.offset[0], -self.offset[1]], float)
        if np.linalg.norm(off) < 1e-9:
            off = np.array([8.0, -8.0])
        h = TEXT_SIZES[self.size]
        prims: list = []
        for k in range(10):
            prims = self._build(t, off * (1.0 + 0.3 * k), h)
            txt = [p for p in prims if isinstance(p, TextPrim)]
            if not any(ctx.collides(text_box(p)) for p in txt):
                break
        ctx.reserve(prims)
        return prims

    def _build(self, t, off, h) -> list:
        side = 1.0 if off[0] >= 0 else -1.0
        elbow = t + off
        land = elbow + np.array([side * 3.0, 0.0])
        marker = {"arrow": "leader-arrow", "dot": "leader-dot", "none": None}[self.arrow]
        lines = self.text.split("\n")
        y0 = land[1] + 0.35 * h - 0.5 * 1.25 * h * (len(lines) - 1)
        return [
            PathPrim(np.array([t, elbow, land]), "leader", marker_start=marker),
            TextPrim(land[0] + side * 1.0, y0, self.text, h, "text leader-text", "start" if side > 0 else "end"),
        ]


@dataclass
class SectionSymbol:
    """A section mark: the cut line p1 -> p2 (model points) with view arrows and labels.
    ``look`` says which side of p1 -> p2 (as seen on the sheet) the section looks towards."""

    p1: PointLike
    p2: PointLike
    label: str = "A"
    look: Literal["left", "right"] = "left"
    size: TextSize = "large"

    def layout(self, ctx: LayoutContext) -> list:
        a, b = ctx.p(self.p1), ctx.p(self.p2)
        d = b - a
        ln = float(np.linalg.norm(d))
        if ln < 1e-9:
            return []
        u = d / ln
        n = np.array([u[1], -u[0]]) * (1.0 if self.look == "left" else -1.0)
        a2, b2 = a - u * 4.0, b + u * 4.0
        h = TEXT_SIZES[self.size]
        prims: list = [PathPrim(np.array([a2, b2]), "section-line")]
        for p in (a2, b2):
            prims.append(PathPrim(np.array([p, p + n * 6.0]), "section-arrow", marker_end="section-head"))
            q = (
                p
                + n * 6.0
                + n * (0.6 * h)
                + (p - 0.5 * (a2 + b2)) / max(np.linalg.norm(p - 0.5 * (a2 + b2)), 1e-9) * 1.5
            )
            prims.append(TextPrim(q[0], q[1] + 0.35 * h, self.label, h, "text section-label", "middle"))
        ctx.reserve(prims)
        return prims


# --------------------------------------------------------------------------------------------
# sheet furniture: placed relative to the laid-out drawing, not to model points
# --------------------------------------------------------------------------------------------


@dataclass
class Caption:
    """A centred caption under the drawing, e.g. ``SIDE ELEVATION``, with the scale under it."""

    text: str
    show_scale: bool = True
    size: TextSize = "header"


@dataclass
class TitleBlock:
    """A boxed title block under the drawing's right edge."""

    title: str
    subtitle: str | None = None
    number: str | None = None
    revision: str | None = None
    date: str | None = None
    author: str | None = None
    show_scale: bool = True
    width: float = 110.0


@dataclass
class ScaleBar:
    """A graphic scale under the drawing. ``length`` in model units (``None``: a round length
    about a quarter of the drawing width), split into ``divisions`` alternating blocks."""

    length: float | None = None
    divisions: int = 4
    position: Literal["bottom-left", "bottom-right"] = "bottom-left"


def nice_length(target: float) -> float:
    if target <= 0:
        return 1.0
    e = math.floor(math.log10(target))
    for m in (5.0, 2.0, 1.0):
        if m * 10**e <= target:
            return m * 10**e
    return 10 ** (e - 1) * 5.0


SheetItem = Caption | TitleBlock | ScaleBar
Annotation = LinearDimension | RadiusDimension | AngleDimension | Text | Leader | SectionSymbol

"""Weld symbols: the basic symbol of each :class:`~ada.api.fasteners.WeldType`, after ISO 2553.

A symbol is plain 2D geometry -- open strokes, closed outlines and filled shapes -- so that every
consumer draws the same thing its own way: the viewer as an SVG icon (:meth:`WeldSymbol.to_svg`), a
fabrication drawing as lines placed on a weld's reference line.

FRAME. A symbol stands on the reference line, ``y = 0``, within the unit box ``0 <= x <= 1``,
``0 <= y <= 1``: the arrow-side position of ISO 2553 system A. :meth:`WeldSymbol.mirrored` hangs it
below the line instead (``y <= 0``), the other-side position. A drawing scales the unit box to its
symbol height and places ``x = 0.5`` where the symbol goes along the line.

The common symbols (fillet, square / V / bevel / Y / U / J butts, backing, plug / slot, spot, seam,
flare, flange, melt-through and the partial-penetration combinations) follow ISO 2553's shapes; the
rarer ones (edge, surfacing, fold, inclined) are simplified forms of them. ``WeldType.NONE`` has none.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ada.api.fasteners import WeldType

__all__ = ["WeldSymbol", "weld_symbol", "weld_symbol_svg"]

Point2 = tuple[float, float]
Path2 = tuple[Point2, ...]


@dataclass(frozen=True)
class WeldSymbol:
    """A weld type's symbol as 2D geometry on the reference line (see the module docstring)."""

    weld_type: WeldType
    #: What the symbol stands for, as ISO 2553 names it.
    description: str
    #: Open polylines.
    strokes: tuple[Path2, ...] = ()
    #: Closed outlines (drawn, not filled).
    outlines: tuple[Path2, ...] = ()
    #: Filled polygons.
    fills: tuple[Path2, ...] = field(default=())

    def paths(self) -> tuple[Path2, ...]:
        return self.strokes + self.outlines + self.fills

    def mirrored(self) -> WeldSymbol:
        """The symbol below the reference line (the other side): every ``y`` negated."""

        def flip(paths: tuple[Path2, ...]) -> tuple[Path2, ...]:
            return tuple(tuple((x, -y) for x, y in p) for p in paths)

        return WeldSymbol(self.weld_type, self.description, flip(self.strokes), flip(self.outlines), flip(self.fills))

    def to_svg(
        self,
        size: int = 24,
        stroke: str = "currentColor",
        stroke_width: float = 0.07,
        reference_line: bool = True,
    ) -> str:
        """A square SVG icon of ``size`` pixels: the symbol, and the reference line under it.

        ``stroke`` colours everything (``currentColor`` takes the text colour where the SVG is
        inlined); ``stroke_width`` is in symbol units (the unit box is 1 wide).
        """
        xs = [x for p in self.paths() for x, _ in p] + ([0.0, 1.0] if reference_line else [])
        ys = [y for p in self.paths() for _, y in p] + ([0.0] if reference_line else [])
        pad = 0.12
        x0, x1 = min(xs) - pad, max(xs) + pad
        y0, y1 = min(ys) - pad, max(ys) + pad
        side = max(x1 - x0, y1 - y0)
        # Square, centred on the symbol; SVG's y runs down, so the frame's y is negated.
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        view = f"{_n(cx - side / 2)} {_n(-cy - side / 2)} {_n(side)} {_n(side)}"

        def pts(p: Path2) -> str:
            return " ".join(f"{_n(x)},{_n(-y)}" for x, y in p)

        common = f'stroke="{stroke}" stroke-width="{_n(stroke_width)}" stroke-linecap="round" stroke-linejoin="round"'
        parts = []
        if reference_line:
            parts.append(f'<line x1="0" y1="0" x2="1" y2="0" {common}/>')
        parts += [f'<polyline points="{pts(p)}" fill="none" {common}/>' for p in self.strokes]
        parts += [f'<polygon points="{pts(p)}" fill="none" {common}/>' for p in self.outlines]
        parts += [f'<polygon points="{pts(p)}" fill="{stroke}" {common}/>' for p in self.fills]
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="{view}">'
            f"<title>{self.description}</title>{''.join(parts)}</svg>"
        )


def weld_symbol(weld_type: WeldType | str) -> WeldSymbol | None:
    """The symbol of ``weld_type`` (a :class:`WeldType` or its name); None for ``NONE``."""
    wt = WeldType.from_str(weld_type) if isinstance(weld_type, str) else weld_type
    return _SYMBOLS.get(wt)


def weld_symbol_svg(weld_type: WeldType | str, **kwargs) -> str | None:
    """:meth:`WeldSymbol.to_svg` of ``weld_type``'s symbol; None for ``NONE``."""
    symbol = weld_symbol(weld_type)
    return symbol.to_svg(**kwargs) if symbol is not None else None


# ── the shapes ───────────────────────────────────────────────────────────────────────────────


def _n(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 12) -> Path2:
    """Points along a circular arc from angle ``a0`` to ``a1`` (degrees, counter-clockwise from +x)."""
    return tuple(
        (
            cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
            cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
        )
        for i in range(n + 1)
    )


def _circle(cx: float, cy: float, r: float) -> Path2:
    return _arc(cx, cy, r, 0, 360, 24)[:-1]


# The pieces several symbols share.
_FILLET: Path2 = ((0.3, 0.0), (0.3, 0.7), (1.0, 0.0))  # vertical leg on the left
_BEVEL: Path2 = ((0.35, 0.8), (0.35, 0.0), (0.85, 0.8))
_SQUARE = ((0.38, 0.0), (0.38, 0.8)), ((0.62, 0.0), (0.62, 0.8))


def _sym(wt: WeldType, description: str, strokes=(), outlines=(), fills=()) -> WeldSymbol:
    return WeldSymbol(wt, description, tuple(strokes), tuple(outlines), tuple(fills))


_SYMBOLS: dict[WeldType, WeldSymbol] = {
    s.weld_type: s
    for s in (
        _sym(WeldType.FILLET, "Fillet weld", outlines=[_FILLET]),
        _sym(WeldType.SQUARE_GROOVE_SQUARE_BUTT, "Square butt weld", strokes=_SQUARE),
        _sym(
            WeldType.BEVEL_GROOVE_SINGLE_V_BUTT, "Single-V butt weld", strokes=[((0.15, 0.8), (0.5, 0.0), (0.85, 0.8))]
        ),
        _sym(WeldType.BEVEL_GROOVE_SINGLE_BEVEL_BUTT, "Single-bevel butt weld", strokes=[_BEVEL]),
        _sym(
            WeldType.SINGLE_V_BUTT_WITH_BROAD_ROOT_FACE,
            "Single-V butt weld with broad root face",
            strokes=[((0.15, 0.8), (0.5, 0.35), (0.85, 0.8)), ((0.5, 0.35), (0.5, 0.0))],
        ),
        _sym(
            WeldType.SINGLE_BEVEL_BUTT_WITH_BROAD_ROOT_FACE,
            "Single-bevel butt weld with broad root face",
            strokes=[((0.35, 0.8), (0.35, 0.0)), ((0.35, 0.35), (0.85, 0.8))],
        ),
        _sym(
            WeldType.U_GROOVE_SINGLE_U_BUTT,
            "Single-U butt weld",
            strokes=[((0.5, 0.0), (0.5, 0.3)), ((0.2, 0.8),) + _arc(0.5, 0.6, 0.3, 180, 360) + ((0.8, 0.8),)],
        ),
        _sym(
            WeldType.J_GROOVE_J_BUTT,
            "Single-J butt weld",
            strokes=[((0.35, 0.0), (0.35, 0.8)), ((0.35, 0.3),) + _arc(0.35, 0.6, 0.3, 270, 360) + ((0.65, 0.8),)],
        ),
        _sym(WeldType.BEVEL_BACKING, "Backing run", strokes=[_arc(0.5, 0.0, 0.3, 0, 180)]),
        _sym(WeldType.PLUG, "Plug weld", strokes=[((0.25, 0.0), (0.25, 0.45), (0.75, 0.45), (0.75, 0.0))]),
        _sym(WeldType.SLOT, "Slot weld", strokes=[((0.15, 0.0), (0.15, 0.35), (0.85, 0.35), (0.85, 0.0))]),
        _sym(WeldType.SPOT, "Spot weld", outlines=[_circle(0.5, 0.0, 0.25)]),
        _sym(
            WeldType.SEAM,
            "Seam weld",
            outlines=[_circle(0.5, 0.0, 0.25)],
            strokes=[((0.1, 0.07), (0.9, 0.07)), ((0.1, -0.07), (0.9, -0.07))],
        ),
        _sym(
            WeldType.FLARE_BEVEL_GROOVE,
            "Flare-bevel butt weld",
            strokes=[((0.35, 0.0), (0.35, 0.8)), _arc(0.85, 0.8, 0.4, 180, 270)],
        ),
        _sym(
            WeldType.FLARE_V_GROOVE,
            "Flare-V butt weld",
            strokes=[_arc(0.1, 0.8, 0.35, 270, 360), _arc(0.9, 0.8, 0.35, 180, 270)],
        ),
        _sym(
            WeldType.EDGE_FLANGE,
            "Butt weld between plates with raised edges",
            strokes=[_arc(0.05, 0.0, 0.35, 0, 90), _arc(0.95, 0.0, 0.35, 90, 180)],
        ),
        _sym(
            WeldType.CORNER_FLANGE,
            "Flanged corner weld",
            strokes=[((0.35, 0.0), (0.35, 0.7)), _arc(0.95, 0.0, 0.45, 90, 180)],
        ),
        _sym(
            WeldType.PARTIAL_PENETRATION_SINGLE_BEVEL_BUTT_PLUS_FILLET,
            "Single-bevel butt weld with fillet (partial penetration)",
            strokes=[_BEVEL],
            outlines=[((0.35, 0.0), (0.35, 0.45), (0.8, 0.0))],
        ),
        _sym(
            WeldType.PARTIAL_PENETRATION_SQUARE_GROOVE_PLUS_FILLET,
            "Square butt weld with fillet (partial penetration)",
            strokes=_SQUARE,
            outlines=[((0.62, 0.0), (0.62, 0.45), (1.0, 0.0))],
        ),
        _sym(WeldType.MELT_THROUGH, "Melt-through", fills=[_arc(0.5, 0.0, 0.3, 0, 180)]),
        _sym(
            WeldType.STEEP_FLANKED_BEVEL_GROOVE_SINGLE_V_BUTT,
            "Steep-flanked single-V butt weld",
            strokes=[((0.3, 0.8), (0.42, 0.0), (0.58, 0.0), (0.7, 0.8))],
        ),
        _sym(
            WeldType.STEEP_FLANKED_BEVEL_GROOVE_SINGLE_BEVEL_BUTT,
            "Steep-flanked single-bevel butt weld",
            strokes=[((0.4, 0.8), (0.4, 0.0), (0.55, 0.0), (0.68, 0.8))],
        ),
        # The rarer ones, simplified.
        _sym(
            WeldType.EDGE,
            "Edge weld",
            strokes=[((0.3, 0.0), (0.3, 0.7)), ((0.5, 0.0), (0.5, 0.7)), ((0.7, 0.0), (0.7, 0.7))],
        ),
        _sym(
            WeldType.ISO_SURFACING,
            "Surfacing",
            strokes=[_arc(0.3, 0.0, 0.2, 0, 180) + _arc(0.7, 0.0, 0.2, 180, 0)[1:]],
        ),
        _sym(WeldType.FOLD, "Fold joint", strokes=[((0.3, 0.0), (0.45, 0.7)), ((0.55, 0.0), (0.7, 0.7))]),
        _sym(WeldType.INCLINED, "Inclined joint", strokes=[((0.25, 0.0), (0.55, 0.7)), ((0.45, 0.0), (0.75, 0.7))]),
    )
}

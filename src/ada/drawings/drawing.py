"""The :class:`Drawing`: objects + a view + annotations -> a dimensioned SVG sheet."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import numpy as np

from ada.drawings.annotations import (
    TEXT_SIZES,
    Annotation,
    Caption,
    DimensionFormat,
    LayoutContext,
    PathPrim,
    ScaleBar,
    SheetItem,
    TextPrim,
    TitleBlock,
    nice_length,
    text_box,
)
from ada.drawings.linework import Linework, LineworkOptions, get_linework_backend
from ada.drawings.scene import BeamStyle, DrawingItem, collect, sphere_mesh
from ada.drawings.svg import GroupStyle, SheetContent, write_svg
from ada.drawings.view import Projector, SectionPlane, Units, View, ViewName, pick_scale


@dataclass
class _Source:
    objects: object
    group: str | None
    beam_style: BeamStyle | None


class Drawing:
    """A 2D drawing of adapy objects.

    Parameters
    ----------
    name
        Title of the SVG document (and the default caption text).
    view
        A :class:`View` or a named view (``"front"``, ``"side"``-like ``"right"``/``"left"``,
        ``"plan"``/``"top"``, ``"iso"``, ...).
    scale
        Scale denominator (``50`` for 1:50). ``None`` picks the largest standard scale at which
        the drawing fits ``max_size`` (paper mm).
    section
        A :class:`SectionPlane`: material in front of it is removed, its cut is drawn heavy.
    backend
        Linework backend: ``"mesh"`` (default) or ``"kernel"`` (exact kernel cut lines); see
        :mod:`ada.drawings.linework`.
    beam_style
        Draw beams as their ``"solid"`` (section outline / sweep), as ``"centerline"``, or ``"both"``.
    show_hidden
        Draw hidden lines (dashed).
    units
        Model units (adapy models are in metres unless built otherwise).
    """

    def __init__(
        self,
        name: str,
        view: View | ViewName = "front",
        scale: float | None = None,
        section: SectionPlane | None = None,
        backend: str = "mesh",
        beam_style: BeamStyle = "solid",
        show_hidden: bool = False,
        hidden_wires: bool = True,
        units: Units = "m",
        dim_format: DimensionFormat | None = None,
        crease_angle: float = 30.0,
        max_size: tuple[float, float] = (380.0, 260.0),
        margin: float = 10.0,
        frame: bool = False,
    ):
        self.name = name
        self.view = View.named(view) if isinstance(view, str) else view
        self.scale = scale
        self.section = section
        self.backend = backend
        self.beam_style: BeamStyle = beam_style
        self.show_hidden = show_hidden
        self.hidden_wires = hidden_wires
        self.units: Units = units
        self.dim_format = dim_format or DimensionFormat(unit=units)
        self.crease_angle = crease_angle
        self.max_size = max_size
        self.margin = margin
        self.frame = frame
        self.annotations: list[Annotation] = []
        self.sheet_items: list[SheetItem] = []
        self.styles: dict[str, GroupStyle] = {}
        self.skipped: list[str] = []
        self._sources: list[_Source] = []
        self._items: list[DrawingItem] | None = None
        self._linework: Linework | None = None
        self._scale_used: float | None = None

    # ------------------------------------------------------------------------------- building
    def add(self, objects, group: str | None = None, beam_style: BeamStyle | None = None) -> "Drawing":
        """Add geometry: a Part/Assembly, an adapy object, a :class:`~ada.drawings.Polyline`, or a
        list of these. ``group`` names the CSS group (default: by object type)."""
        self._sources.append(_Source(objects, group, beam_style))
        self._items = None
        self._linework = None
        return self

    def annotate(self, *annotations: Annotation | SheetItem) -> "Drawing":
        """Add dimensions, text, leaders, section symbols, or sheet items (caption, title block,
        scale bar)."""
        for a in annotations:
            if isinstance(a, (Caption, TitleBlock, ScaleBar)):
                self.sheet_items.append(a)
            else:
                self.annotations.append(a)
        return self

    def style(
        self,
        group: str,
        color: str | None = None,
        width: float | None = None,
        cut_fill: str | None = None,
        dash: str | None = None,
    ) -> "Drawing":
        """Style a group's linework: stroke ``color``, projection ``width`` (paper mm), cut fill
        (a colour or ``"hatch"``), and a ``dash`` array for its projection lines."""
        self.styles[group] = GroupStyle(color=color, width=width, cut_fill=cut_fill, dash=dash)
        return self

    # ------------------------------------------------------------------------------- geometry
    def items(self) -> list[DrawingItem]:
        if self._items is None:
            self.skipped = []
            items: list[DrawingItem] = []
            for src in self._sources:
                bs = src.beam_style or self.beam_style
                items += collect(src.objects, src.group, bs, -1.0, self.skipped)
            self._items = items
        return self._items

    def _screen_extent(self) -> tuple[np.ndarray, np.ndarray]:
        pts = []
        for it in self.items():
            if it.sphere is not None:
                c, r = it.sphere
                cs = self.view.to_screen(c)[0]
                pts += [cs[:2] - r, cs[:2] + r]
                continue
            b = it.bounds()
            if b is None:
                continue
            lo, hi = b
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            pts += list(self.view.to_screen(corners)[:, :2])
        if not pts:
            return np.zeros(2), np.ones(2)
        a = np.asarray(pts)
        return a.min(axis=0), a.max(axis=0)

    @property
    def scale_used(self) -> float:
        if self._scale_used is None:
            if self.scale is not None:
                self._scale_used = float(self.scale)
            else:
                lo, hi = self._screen_extent()
                ext = (float((hi - lo)[0]), float((hi - lo)[1]))
                self._scale_used = pick_scale(ext, self.max_size, self.units)
        return self._scale_used

    def projector(self) -> Projector:
        return Projector(self.view, self.scale_used, self.units)

    def linework(self) -> Linework:
        """The 2D linework (screen coordinates, model units)."""
        if self._linework is None:
            pr = self.projector()
            items = self.items()
            lo, hi = self._screen_extent()
            size = max(float(np.linalg.norm(hi - lo)), 1e-9)
            defl = 0.1 / pr.k  # 0.1 mm on paper
            for it in items:
                if it.sphere is not None:
                    it.vertices, it.faces = sphere_mesh(it.sphere[0], it.sphere[1], defl)
            opts = LineworkOptions(
                tolerance=max(1e-7 * size, 1e-9),
                sample_spacing=0.3 / pr.k,
                crease_angle=self.crease_angle,
                show_hidden=self.show_hidden,
                hidden_wires=self.hidden_wires,
            )
            self._linework = get_linework_backend(self.backend).compute(items, self.view, self.section, opts)
        return self._linework

    # ------------------------------------------------------------------------------- output
    def sheet(self) -> SheetContent:
        pr = self.projector()
        lw = self.linework()
        strokes = [(pr.screen_to_paper(s.points), s.kind, s.group) for s in lw.strokes]
        regions = [([pr.screen_to_paper(lp) for lp in r.loops], r.group) for r in lw.regions]
        allpts = [p for p, _, _ in strokes] + [lp for loops, _ in regions for lp in loops]
        if allpts:
            a = np.vstack(allpts)
            content = (a.min(axis=0), a.max(axis=0))
        else:
            content = (np.zeros(2), np.zeros(2))
        ctx = LayoutContext(pr, self.dim_format, content)
        prims: list = []
        for ann in self.annotations:
            prims += ann.layout(ctx)
        lo, hi = _bounds(content, prims)
        prims += self._sheet_furniture(lo, hi, pr)
        lo, hi = _bounds((lo, hi), prims)
        m = self.margin
        shift = np.array([m, m]) - lo
        w, h = float(hi[0] - lo[0] + 2 * m), float(hi[1] - lo[1] + 2 * m)

        def mv(a):
            return a + shift

        sheet = SheetContent(width=w, height=h, title=self.name)
        sheet.strokes = [(mv(p), k, g) for p, k, g in strokes]
        sheet.regions = [([mv(lp) for lp in loops], g) for loops, g in regions]
        for pr_ in prims:
            if isinstance(pr_, PathPrim):
                sheet.prims.append(PathPrim(mv(pr_.points), pr_.cls, pr_.closed, pr_.marker_start, pr_.marker_end))
            elif isinstance(pr_, TextPrim):
                t = TextPrim(**{**pr_.__dict__})
                t.x, t.y = t.x + shift[0], t.y + shift[1]
                sheet.prims.append(t)
        if self.frame:
            sheet.prims.append(
                PathPrim(np.array([[2, 2], [w - 2, 2], [w - 2, h - 2], [2, h - 2]]), "sheet-frame", True)
            )
        sheet.css = "\n".join(st.css(g) for g, st in self.styles.items())
        sheet.metadata = {"scale": f"1:{_scale_text(self.scale_used)}", "units": self.units}
        if self.view.name:
            sheet.metadata["view"] = self.view.name
        return sheet

    def to_svg(self, path: str | pathlib.Path | None = None) -> str:
        """The drawing as an SVG document; also written to ``path`` when given."""
        svg = write_svg(self.sheet())
        if path is not None:
            p = pathlib.Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(svg, encoding="utf-8")
        return svg

    def to_png(self, path: str | pathlib.Path, dpi: float = 200.0) -> pathlib.Path:
        """Rasterise with cairosvg (optional dependency)."""
        try:
            import cairosvg
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("PNG output needs cairosvg (conda/pip install cairosvg)") from exc
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        cairosvg.svg2png(bytestring=self.to_svg().encode("utf-8"), write_to=str(p), dpi=dpi)
        return p

    # ------------------------------------------------------------------------------- furniture
    def _sheet_furniture(self, lo: np.ndarray, hi: np.ndarray, pr: Projector) -> list:
        prims: list = []
        y = float(hi[1]) + 6.0
        bars = [s for s in self.sheet_items if isinstance(s, ScaleBar)]
        captions = [s for s in self.sheet_items if isinstance(s, Caption)]
        blocks = [s for s in self.sheet_items if isinstance(s, TitleBlock)]
        scale_txt = f"1:{_scale_text(self.scale_used)}"
        for sb in bars:
            width = float(hi[0] - lo[0])
            length = sb.length or nice_length(0.25 * max(width, 20.0) / pr.k)
            seg = length * pr.k / sb.divisions
            total = seg * sb.divisions
            x0 = float(lo[0]) if sb.position == "bottom-left" else float(hi[0]) - total
            for i in range(sb.divisions):
                cls = "scale-bar-fill" if i % 2 == 0 else "scale-bar"
                xa = x0 + i * seg
                prims.append(
                    PathPrim(np.array([[xa, y], [xa + seg, y], [xa + seg, y + 1.5], [xa, y + 1.5]]), cls, True)
                )
            h = TEXT_SIZES["small"]
            for i in (0, sb.divisions):
                v = length * i / sb.divisions
                prims.append(TextPrim(x0 + i * seg, y + 1.5 + 1.2 * h, _num(v), h, "text small", "middle", 0.0, False))
            prims.append(TextPrim(x0 + total + 2.0, y + 1.5, self.units, h, "text small", "start", 0.0, False))
            y += 1.5 + 1.2 * h + 4.0
        for cap in captions:
            h = TEXT_SIZES[cap.size]
            cx = 0.5 * float(lo[0] + hi[0])
            prims.append(TextPrim(cx, y + h, cap.text, h, "text caption", "middle", 0.0, False))
            y += h + 2.0
            if cap.show_scale:
                hs = TEXT_SIZES["regular"]
                prims.append(TextPrim(cx, y + hs, f"SCALE {scale_txt}", hs, "text", "middle", 0.0, False))
                y += hs + 2.0
            y += 3.0
        for tb in blocks:
            rows: list[tuple[str, str]] = []
            if tb.subtitle:
                rows.append(("", tb.subtitle))
            for k, v in (("No.", tb.number), ("Rev.", tb.revision), ("Date", tb.date), ("By", tb.author)):
                if v:
                    rows.append((k, v))
            if tb.show_scale:
                rows.append(("Scale", scale_txt))
            ht, hr = TEXT_SIZES["large"], TEXT_SIZES["small"]
            row_h = hr + 2.2
            height = ht + 4.0 + row_h * len(rows)
            x1 = max(float(hi[0]), float(lo[0]) + tb.width)
            x0 = x1 - tb.width
            box = np.array([[x0, y], [x1, y], [x1, y + height], [x0, y + height]])
            prims.append(PathPrim(box, "title-block", True))
            prims.append(TextPrim(x0 + 2.0, y + ht + 1.5, tb.title, ht, "text large", "start", 0.0, False))
            yy = y + ht + 4.0
            for k, v in rows:
                prims.append(PathPrim(np.array([[x0, yy], [x1, yy]]), "title-block-rule"))
                if k:
                    prims.append(TextPrim(x0 + 2.0, yy + hr + 0.8, k, hr, "text small", "start", 0.0, False))
                prims.append(
                    TextPrim(x0 + (20.0 if k else 2.0), yy + hr + 0.8, v, hr, "text small", "start", 0.0, False)
                )
                yy += row_h
            y += height + 3.0
        return prims


def _bounds(content, prims) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = np.array(content[0], float), np.array(content[1], float)
    for p in prims:
        if isinstance(p, PathPrim) and len(p.points):
            lo = np.minimum(lo, p.points.min(axis=0) - 1.5)
            hi = np.maximum(hi, p.points.max(axis=0) + 1.5)
        elif isinstance(p, TextPrim):
            a, b = text_box(p)
            lo, hi = np.minimum(lo, a - 0.6), np.maximum(hi, b + 0.6)
    return lo, hi


def _scale_text(s: float) -> str:
    return str(int(s)) if abs(s - round(s)) < 1e-9 else f"{s:g}"


def _num(v: float) -> str:
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:g}" if v >= 0.01 else f"{v:.3g}"

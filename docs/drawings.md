# Drawings

`ada.drawings` makes dimensioned 2D drawings of adapy objects and writes them as standalone SVG.
You give it a group of objects, a view (with an optional section cut) and a list of annotations.
The SVG it writes is in paper millimetres at a stated scale, with CSS classes for each kind of line.

```python
import ada
from ada.drawings import Caption, Drawing, Leader, LinearDimension, ScaleBar, SectionPlane
from ada.drawings.anchors import bbox_point, beam_end

col = ada.Beam("col", (0, 0, 0), (0, 0, 5), "HEB300")
rafter = ada.Beam("rafter", (0, 0, 5), (8, 0, 5), "IPE400")
tank = ada.PrimCyl("tank", (4, 0, 0.3), (4, 0, 3.3), 1.1)
part = ada.Part("frame") / [col, rafter, tank]

d = Drawing("Portal frame", view="front", scale=50)
d.add(part)
d.annotate(
    LinearDimension(beam_end(col, "start"), beam_end(col, "end"), "vertical"),
    Leader(bbox_point(tank, 1.0, 0.5, 0.5), "tank Ø2.2", offset=(12, 4)),
    Caption("FRONT ELEVATION"),
    ScaleBar(),
)
d.to_svg("portal_front.svg")
```

A complete example, with a section, a title block and an isometric view with hidden lines, is in
`examples/drawings_portal_frame.py`.

## Views and sections

- `view` takes a named view (`"front"`, `"back"`, `"left"`, `"right"`, `"plan"`/`"top"`,
  `"bottom"`, `"iso"`) or a `View(direction, up, origin, depth)`.
  - `direction` is the look direction.
  - `depth=(near, far)` limits what is drawn, without drawing a cut.
- `section=SectionPlane(origin, normal)` cuts the model.
  - Material on the side `normal` points to is kept; the rest is removed.
  - The cut is drawn heavy, with a fill (`style(group, cut_fill="hatch")` hatches it).
  - `View.section_at(point)` builds the plane facing the view.
- `scale=50` means 1:50. If you leave it out, the drawing picks the largest standard scale that
  fits `max_size`.

## Linework

- `beam_style` chooses how beams are drawn: as solids (`"solid"`), as dash-dot centre lines
  (`"centerline"`), or both.
- `Polyline(points)` draws lines that are not objects of their own: cables, guys, construction
  lines.
- `show_hidden=True` adds hidden lines, dashed.

The lines come from a *linework backend*:

| backend    | cut lines                                    | projection lines                                    |
|------------|----------------------------------------------|-----------------------------------------------------|
| `"mesh"`   | the tessellation sliced by the plane         | crease, boundary and silhouette edges of the tessellation, with hidden-line removal |
| `"kernel"` | the CAD kernel's exact plane section (`CadBackend.section_with_plane`) | as `"mesh"`                    |

- Both backends use adapy's active CAD backend for the tessellation, so they don't depend on any
  IFC tooling.
- Spheres are drawn with a true circular outline.
- Hidden-line removal tests sample points along each edge against the triangles in front of
  them. When there is a cut, it also tests them against the section caps.
- Other kernels plug in with `register_linework_backend(name, factory)`.

## Annotations

Annotations are placed at model points. Helpers in `ada.drawings.anchors` take those points from
objects: `beam_end`, `beam_point`, `bbox_point`. Offsets and text heights are in paper millimetres.

| annotation        | what it draws |
|-------------------|---------------|
| `LinearDimension` | `"horizontal"`, `"vertical"` or `"aligned"`. Its offset can be automatic (away from the drawing's centre), signed mm, or `"outside"` the drawing. If it would collide with other text, it moves out one tier at a time. |
| `RadiusDimension` | radius or diameter of a circle seen face-on |
| `AngleDimension`  | an angle arc with its value |
| `Text`, `Leader`  | labels and callouts; a leader moves its text out when it would overlap other text |
| `SectionSymbol`   | a section mark with view arrows and labels |
| `Caption`, `TitleBlock`, `ScaleBar` | placed under the drawing |

`DimensionFormat(unit, precision, show_unit)` sets how values print.

## Styling

The SVG uses the classes `cut`, `cut-fill`, `projection`, `hidden`, `centerline`, `wire`,
`dimension`, `leader` and `text`. Arrowheads are SVG markers.

Each object group also gets a `g-<group>` class. The default group is the object type (`beam`,
`plate`, `pipe`, `shape`, `wire`); pass `add(objects, group=...)` to set your own. Restyle a group
with `drawing.style(group, color=..., width=..., cut_fill=..., dash=...)`.

`Drawing.to_png(path)` rasterises the SVG when `cairosvg` is installed.

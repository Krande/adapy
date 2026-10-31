"""One tessellation against another, element by element, as a report.

A DIFFERENCE IS NOT A VERDICT. Either side may be stale or tessellated at a chord nobody here chose,
so this reports WHERE the two disagree and by how much; which side is wrong is a question for whoever
looks. The one thing it asserts is the join: ``compared`` near zero means the element names did not
line up, and that is a broken run rather than a model that agrees.

The pass rule is the bounding-box rule (per face, ``tolerance_mm``); ``volume_ratio`` and
``triangle_ratio`` are reported, never gated on -- a box cannot tell "rotated half a degree" from
"wrong profile", and those two can.
"""

from __future__ import annotations

import datetime as _dt
from typing import Mapping

from . import glb
from .glb import Bounds, Element

__all__ = ["DEFAULT_LIMIT", "DEFAULT_TOLERANCE_MM", "SCHEMA_VERSION", "compare", "compare_files", "summarise"]

SCHEMA_VERSION = 1
DEFAULT_TOLERANCE_MM = 1.0
DEFAULT_LIMIT = 500


def _bounds_delta_mm(ours: Bounds, theirs: Bounds, scale: float) -> tuple[list[float], list[float], float]:
    centre = [(a - b) * scale for a, b in zip(ours.centre, theirs.centre)]
    size = [(a - b) * scale for a, b in zip(ours.size, theirs.size)]
    worst = max(abs(v) for v in centre + size)
    return centre, size, worst


def _ratio(ours: float, theirs: float) -> float | None:
    """``ours / theirs``, or None where the denominator cannot carry a ratio -- "we have volume and
    they have none" and "the two are equal" must not both read as a number someone might average."""
    if theirs == 0:
        return None
    return ours / theirs


def compare(
    ours: Mapping[str, Element],
    theirs: Mapping[str, Element],
    *,
    root: str,
    tolerance_mm: float = DEFAULT_TOLERANCE_MM,
    limit: int = DEFAULT_LIMIT,
    theirs_is_y_up: bool = True,
    units_to_mm: float = 1000.0,
    now: _dt.datetime | None = None,
) -> dict:
    """The report. ``ours`` and ``theirs`` are ``name -> Element`` as loaded, both re-keyed through
    ``glb.index_by_name`` here so the normalisation that decides what joins is in one place.

    ``theirs_is_y_up``: their boxes are Y-up (glTF) while ours are Z-up -- theirs are rotated before
    the comparison. ``units_to_mm`` scales both sides' units (glTF metres by default)."""
    left = glb.index_by_name(ours.values())
    right = glb.index_by_name(theirs.values())

    differences = []
    compared = 0
    for key, a in left.items():
        b = right.get(key)
        if b is None:
            continue
        compared += 1
        b_bounds = glb.y_up_to_z_up(b.bounds) if theirs_is_y_up else b.bounds
        centre, size, worst = _bounds_delta_mm(a.bounds, b_bounds, units_to_mm)
        if worst <= tolerance_mm:
            continue
        differences.append(
            {
                "name": a.name,
                "centre_delta_mm": centre,
                "size_delta_mm": size,
                "worst_mm": worst,
                # A flipped axis makes one volume negative; the magnitude is what a ratio is about.
                "volume_ratio": _ratio(abs(a.volume), abs(b.volume)),
                "triangle_ratio": _ratio(a.triangles, b.triangles),
                "ours": {"min": list(a.bounds.min), "max": list(a.bounds.max)},
                "theirs": {"min": list(b_bounds.min), "max": list(b_bounds.max)},
            }
        )

    differences.sort(key=lambda d: d["worst_mm"], reverse=True)
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "root": root,
        "generated_at": stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tolerance_mm": tolerance_mm,
        "compared": compared,
        "differing": len(differences),
        "only_ours": sorted(k for k in left if k not in right),
        "only_theirs": sorted(k for k in right if k not in left),
        # Capped so a report stays readable; `differing` never is, so the cap cannot flatter a run.
        "differences": differences[:limit],
    }


def summarise(report: Mapping) -> str:
    """One line, saying the thing that decides what to do next. Zero compared is FIRST and phrased as
    a failure: it is the one a cheerful "none differing" would hide."""
    compared = report.get("compared", 0)
    if not compared:
        return (
            f"nothing compared -- no element name matched on both sides "
            f"({len(report.get('only_ours', []))} ours, {len(report.get('only_theirs', []))} theirs). "
            "That is a broken join, not agreement."
        )
    tol = report.get("tolerance_mm", DEFAULT_TOLERANCE_MM)
    differing = report.get("differing", 0)
    if not differing:
        return f"{compared} elements compared, none differing by more than {tol:g} mm"
    worst = report["differences"][0] if report.get("differences") else None
    tail = f" -- worst {worst['name']} at {worst['worst_mm']:.1f} mm" if worst else ""
    return f"{differing} of {compared} elements differ by more than {tol:g} mm{tail}"


def compare_files(ours_path, theirs_path, *, root: str, **kw) -> dict:
    """:func:`compare` on two GLB paths, gzipped or not."""
    return compare(glb.load(ours_path), glb.load(theirs_path), root=root, **kw)

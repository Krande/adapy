"""The native IFC reader measured against ifcopenshell, feature by feature, on third-party files.

ifcopenshell is the oracle here (a test dependency only). Every IFC file under ``files/ifc_files``
(IFC2X3, IFC4, IFC4X1, IFC4X3) is read twice: by ifcopenshell directly, and by the native backend
(``from_ifc(reader="native")`` plus the native product query). Each feature is one test:

- ``products``: the products with a body (GlobalIds) are the same.
- ``names``: each product's Name.
- ``classes``: each product's IFC class, where the native side states one.
- ``attributes``: Description / ObjectType / Tag.
- ``psets``: property sets (quantity sets are not compared).
- ``materials``: the associated material's name.
- ``spatial_parent``: the nearest parent: the assembly or spatial element holding the product.
- ``units``: the length unit (metres per file unit).
- ``geometry``: each product's world bounding box, in metres, within tolerance.

A difference the native side is known not to cover yet is an xfail with the reason in
``KNOWN_GAPS``, so the run is a gap report: an xfail is a gap, a pass is parity, and a failure is a
regression or a new gap to list.
"""

from __future__ import annotations

import functools
import math
import pathlib

import numpy as np
import pytest

import ada
from ada.cadit.ifc import backend as ib

pytest.importorskip("ifcopenshell")
import ifcopenshell.geom  # noqa: E402
import ifcopenshell.util.element as ifc_el  # noqa: E402
import ifcopenshell.util.unit as ifc_unit  # noqa: E402

pytestmark = pytest.mark.skipif(not ib.NativeIfcBackend().available(), reason="adacpp without IfcNgeomStream")

ROOT = pathlib.Path(__file__).resolve().parents[5] / "files" / "ifc_files"
CORPUS = sorted(p for p in ROOT.rglob("*.ifc"))
FEATURES = (
    "products",
    "names",
    "classes",
    "attributes",
    "psets",
    "materials",
    "spatial_parent",
    "units",
    "geometry",
)

#: feature -> (reason, files it applies to, or None for every file). A listed gap is an xfail.
#: Keep the reasons specific; they are the gap list.
KNOWN_GAPS: dict[str, tuple[str, set[str] | None]] = {
    "attributes": ("the native reader carries no Description / ObjectType / Tag", None),
    "psets": ("the native reader carries property sets for members it rebuilds only", None),
    "materials": (
        "the native reader states materials for beam/column/member/plate/slab members only",
        {"bath-csg-solid.ifc", "reinforcing-assembly.ifc", "reinforcing-stirrup.ifc"},
    ),
    "spatial_parent": (
        "an alignment segment natively has no parent (ifcopenshell: the site)",
        {"fixed-reference-swept-area-solid.ifc"},
    ),
    "units": (
        "the native IFC stream reads only a plain SI length unit: an inch unit (IfcConversionBasedUnit) "
        "and this file's millimetre assignment come back as metres",
        {
            "bs_samples/column-straight-rectangle-tessellation.ifc",
            "bs_samples/polygonal-face-tessellation.ifc",
            "bs_samples/tessellation-with-image-texture.ifc",
        },
    ),
    "geometry": (
        "no native body for a CSG IfcBooleanResult, an IfcSweptDiskSolid, a sectioned or fixed-reference "
        "sweep along an alignment, or (before adacpp 0.48) a half-space-clipped beam; and the unit gap above",
        {
            "bath-csg-solid.ifc",
            "bs_samples/column-straight-rectangle-tessellation.ifc",
            "bs_samples/polygonal-face-tessellation.ifc",
            "fixed-reference-swept-area-solid.ifc",
            "half_space_beam.ifc",
            "reinforcing-assembly.ifc",
            "reinforcing-stirrup.ifc",
            "sectioned-solid-horizontal.ifc",
        },
    ),
}


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).as_posix()


# ---------------------------------------------------------------- oracle (ifcopenshell)


@functools.lru_cache(maxsize=None)
def _oracle(path: pathlib.Path) -> dict:
    from ada.cadit.ifc.store import open_ifc_file

    f = open_ifc_file(path)
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    boxes: dict[str, tuple | None] = {}
    it = ifcopenshell.geom.iterator(settings, f)
    if it.initialize():
        while True:
            shape = it.get()
            verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
            # A body has faces; an alignment's or an axis' curve meshes to vertices only, so it
            # counts as a product but has no box to compare.
            if len(verts):
                boxes[shape.guid] = (verts.min(0), verts.max(0)) if len(shape.geometry.faces) else None
            if not it.next():
                break
    products: dict[str, dict] = {}
    for guid in boxes:
        p = f.by_guid(guid)
        # The nearest parent: the assembly or spatial element it is aggregated into, else the
        # spatial element containing it. An unnamed parent goes by its GlobalId, as natively.
        parent = ifc_el.get_aggregate(p) or ifc_el.get_container(p)
        psets = ifc_el.get_psets(p, psets_only=True)
        products[guid] = {
            "class": p.is_a(),
            "name": p.Name,
            "attributes": {k: getattr(p, k, None) for k in ("Description", "ObjectType", "Tag") if getattr(p, k, None)},
            "psets": {k: {pk: pv for pk, pv in v.items() if pk != "id"} for k, v in psets.items()},
            "materials": {m.Name for m in ifc_el.get_materials(p) if getattr(m, "Name", None)},
            "parent": (parent.Name or parent.GlobalId) if parent is not None else None,
            "bbox": boxes[guid],
        }
    return {"products": products, "unit_scale": float(ifc_unit.calculate_unit_scale(f))}


# ---------------------------------------------------------------- native


@functools.lru_cache(maxsize=None)
def _native(path: pathlib.Path) -> dict:
    import adacpp

    from ada.base.units import Units
    from ada.visit.tessellate import BatchTessellator

    a = ada.from_ifc(path, reader="native")
    to_m = 0.001 if a.units == Units.MM else 1.0
    query = {r.guid: r for r in ib.get_ifc_backend("native").query(path)}
    bt = BatchTessellator()
    products: dict[str, dict] = {}
    for obj in a.get_all_physical_objects():
        pts = []
        try:
            for ms in bt.batch_tessellate([obj]):
                v = np.asarray(ms.position, dtype=float).reshape(-1, 3)
                if getattr(ms, "matrix", None) is not None:
                    m = np.asarray(ms.matrix, dtype=float).reshape(4, 4)
                    v = (np.c_[v, np.ones(len(v))] @ m.T)[:, :3]
                pts.append(v)
        except Exception:  # noqa: BLE001 - an untessellable product shows as a geometry gap
            pts = []
        parent = obj.parent
        q = query.get(obj.guid)
        products[obj.guid] = {
            "class": q.ifc_class if q else None,
            "name": obj.name,
            "attributes": dict(q.attributes) if q else {},
            "psets": dict(q.psets) if q else {},
            "material": q.material if q else None,
            "parent": None if parent is None or parent is a else parent.name,
            "bbox": (np.vstack(pts).min(0) * to_m, np.vstack(pts).max(0) * to_m) if pts else None,
            "type": type(obj).__name__,
        }
    return {"products": products, "unit_scale": float(adacpp.cad.IfcNgeomStream(str(path)).unit_scale)}


# ---------------------------------------------------------------- comparisons


def _norm(v):
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    return v


def _camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def _diff(path: pathlib.Path, feature: str) -> list[str]:
    o, n = _oracle(path), _native(path)
    op, np_ = o["products"], n["products"]
    out: list[str] = []
    if feature == "products":
        missing = sorted(set(op) - set(np_))
        extra = sorted(set(np_) - set(op))
        if missing:
            out.append(f"{len(missing)} of {len(op)} missing natively, e.g. {[op[g]['class'] for g in missing[:5]]}")
        if extra:
            out.append(f"{len(extra)} extra natively")
        return out
    if feature == "units":
        if math.isclose(o["unit_scale"], n["unit_scale"], rel_tol=1e-9):
            return []
        return [f"metres per file unit: ifcopenshell {o['unit_scale']}, native {n['unit_scale']}"]
    for g in sorted(set(op) & set(np_)):
        a, b = op[g], np_[g]
        if feature == "names":
            want = a["name"] or g
            if b["name"] != want:
                out.append(f"{g}: {want!r} != {b['name']!r}")
        elif feature == "classes":
            if b["class"] is None:
                out.append(f"{g}: {a['class']} not stated")
            elif b["class"].upper() != a["class"].upper():
                out.append(f"{g}: {a['class']} != {b['class']}")
        elif feature == "attributes":
            got = {k[0].upper() + _camel(k[1:]): v for k, v in b["attributes"].items()}
            if _norm(a["attributes"]) != _norm(got):
                out.append(f"{g} ({a['class']}): {a['attributes']} != {b['attributes']}")
        elif feature == "psets":
            if a["psets"] and _norm(a["psets"]) != _norm(b["psets"]):
                out.append(f"{g} ({a['class']}): {sorted(a['psets'])} != {sorted(b['psets'])}")
        elif feature == "materials":
            if a["materials"] and b["material"] not in a["materials"]:
                out.append(f"{g} ({a['class']}): {sorted(a['materials'])} does not hold {b['material']!r}")
        elif feature == "spatial_parent":
            if a["parent"] != b["parent"]:
                out.append(f"{g} ({a['class']}): {a['parent']!r} != {b['parent']!r}")
        elif feature == "geometry":
            if a["bbox"] is None:
                continue  # a curve, not a body
            if b["bbox"] is None:
                out.append(f"{g} ({a['class']}, native {b['type']}): no native geometry")
                continue
            lo, hi = a["bbox"]
            tol = max(2e-3, 0.01 * float(np.linalg.norm(hi - lo)))
            d = max(float(np.abs(lo - b["bbox"][0]).max()), float(np.abs(hi - b["bbox"][1]).max()))
            if d > tol:
                out.append(f"{g} ({a['class']}, native {b['type']}): bbox off by {d:.4g} m (tol {tol:.3g})")
    return out


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("path", CORPUS, ids=[_rel(p) for p in CORPUS])
def test_native_matches_ifcopenshell(path, feature):
    try:
        problems = _diff(path, feature)
    except Exception as exc:  # noqa: BLE001 - a reader failure on a file is a gap of every feature
        problems = [f"read failed: {type(exc).__name__}: {exc}"]
    if not problems:
        return
    head = f"{_rel(path)} [{feature}]: {len(problems)} difference(s): " + "; ".join(problems[:5])
    gap = KNOWN_GAPS.get(feature)
    if gap is not None and (gap[1] is None or _rel(path) in gap[1]):
        pytest.xfail(f"{gap[0]} -- {head}")
    pytest.fail(head)

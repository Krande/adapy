"""Process-global cache for OCC bodies derived from parametric
adapy objects.

The point of the cache is twofold:

* **Performance** — ``geom_to_occ_geom`` walks the parametric
  description through OCCT's builder API; repeating that on every
  ``solid_occ()`` call adds up fast when tessellator / IFC writer /
  clash checker all want the same body.
* **Serialisability** — adapy objects must stay picklable across
  process boundaries (multiprocessing fork in the audit worker,
  joblib, cache layers, plain ``copy.deepcopy``). Storing OCC
  bodies on the object itself breaks that. The cache lives here,
  keyed by ``obj.guid`` — the object carries only its parametric
  description and rebuilds the OCC body on demand in any process.

Two caches share the same machinery: one for solid bodies (most
shapes) and one for shell bodies (Plate / Beam where a thin-shell
representation is sometimes preferred).

Invalidation: keyed by ``guid`` which never changes for the life
of an object. Mutating an object's parametric attributes does NOT
evict the cached body — call :func:`invalidate` explicitly after a
mutation if the change should propagate. For audit / convert
flows the objects are constructed fresh per job so the issue
doesn't surface in practice.

The body is built through the active CAD backend
(``ada.cad.active_backend().build(...)``) rather than calling the
OCC builders directly — this is the construction seam for the
backend abstraction. The returned handle is a ``TopoDS_Shape`` under
the (default) pythonocc backend. Cache keys are namespaced by backend
name so a mid-run backend switch can never mix handle types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from ada.cad import active_backend

if TYPE_CHECKING:
    from collections.abc import Iterable

    from OCC.Core.TopoDS import TopoDS_Shape

    from ada.base.physical_objects import BackendGeom

# — module-level dicts so the objects stay alive —
occ_solid_cache: Dict[str, TopoDS_Shape] = {}
occ_shell_cache: Dict[str, TopoDS_Shape] = {}


def _key(occ_object: "BackendGeom") -> str:
    return f"{active_backend().name}:{occ_object.guid}"


def get_solid_occ(occ_object: "BackendGeom") -> TopoDS_Shape:
    """Return (and cache) the OCC solid body for any parametric
    ``BackendGeom`` — ``solid_geom()`` is the contract this leans
    on. ``Plate``, ``Beam``, ``PrimBox``, ``PrimCyl``, ``PrimSphere``,
    ``PrimCone``, ``PrimExtrude``, ``PrimRevolve``, ``PrimSweep``,
    ``Wall``, ``Pipe*`` all implement it.
    """
    key = _key(occ_object)
    if key not in occ_solid_cache:
        occ_solid_cache[key] = active_backend().build(occ_object.solid_geom())
    return occ_solid_cache[key]


def prebuild_solids(objects: "Iterable[BackendGeom]", threads: int = 0) -> "list[str | None]":
    """Build and cache the solid bodies of many objects in one go, so a later
    :func:`get_solid_occ` / ``obj.solid_occ()`` (and everything built on it: ``to_gltf``, the
    clash checks, the IFC/STEP writers) finds them already built.

    Faceted B-rep bodies (weld beads and other ``FacetedBrep`` shapes of plain polygons) are
    built together, in parallel on ``threads`` threads (0: every core) and outside the GIL, when
    the active backend is adacpp and its ada-cpp has ``solids_from_polygons_batch``; every other
    body is built one by one through the backend, exactly as :func:`get_solid_occ` would. Objects
    whose body is already cached are skipped.

    Returns one entry per object, in order: None where the body is cached (now or before), else
    the reason it could not be built -- nothing is cached for that object, and a later
    ``solid_occ()`` on it raises as it always did. Never raises for one object's failure.
    """
    from ada.cad import build_solids

    objects = list(objects)
    errors: list[str | None] = [None] * len(objects)
    todo: dict[str, list[int]] = {}  # cache key -> the objects sharing it (same guid)
    geometries = []
    for i, obj in enumerate(objects):
        key = _key(obj)
        if key in occ_solid_cache:
            continue
        if key in todo:
            todo[key].append(i)
            continue
        try:
            geometries.append(obj.solid_geom())
        except Exception as exc:  # noqa: BLE001 - reported per object, by design
            errors[i] = f"{type(exc).__name__}: {exc}"
            continue
        todo[key] = [i]
    if not geometries:
        return errors
    shapes, build_errors = build_solids(geometries, threads=threads)
    for (key, idx), shape, err in zip(todo.items(), shapes, build_errors):
        if shape is not None:
            occ_solid_cache[key] = shape
        else:
            for i in idx:
                errors[i] = err
    return errors


def cached_solid_by_guid(guid: str) -> TopoDS_Shape:
    """Look up an already-built solid body by raw object ``guid`` for the
    active backend. Raises ``KeyError`` if it has not been built yet (call
    :func:`get_solid_occ` first). Keeps the backend-namespaced key format
    encapsulated for callers that only hold a guid (e.g. the clash LRU)."""
    return occ_solid_cache[f"{active_backend().name}:{guid}"]


def get_shell_occ(occ_object: "BackendGeom") -> TopoDS_Shape:
    """Same for shell geometry. Requires the object to implement
    ``shell_geom()`` — currently ``Plate``, ``Beam``, ``Pipe*``,
    ``Wall``."""
    key = _key(occ_object)
    if key not in occ_shell_cache:
        occ_shell_cache[key] = active_backend().build(occ_object.shell_geom())
    return occ_shell_cache[key]


def invalidate(guid: str) -> None:
    """Drop the cached solid + shell for one object across all backends.
    Callers that mutate a Beam / Plate / Prim* parametric description after
    the body has been built use this to force a rebuild on the next
    ``solid_occ()`` / ``shell_occ()`` call."""
    suffix = f":{guid}"
    for cache in (occ_solid_cache, occ_shell_cache):
        for k in [k for k in cache if k.endswith(suffix)]:
            cache.pop(k, None)


def clear_all() -> None:
    """Drop the entire cache. Test isolation + long-running daemon
    pruning."""
    occ_solid_cache.clear()
    occ_shell_cache.clear()

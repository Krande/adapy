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

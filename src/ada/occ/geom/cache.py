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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Solid

from ada.occ.geom import geom_to_occ_geom

if TYPE_CHECKING:
    from ada.base.physical_objects import BackendGeom

# — module-level dicts so the objects stay alive —
occ_solid_cache: Dict[str, TopoDS_Shape] = {}
occ_shell_cache: Dict[str, TopoDS_Shape] = {}


def get_solid_occ(occ_object: "BackendGeom") -> TopoDS_Shape:
    """Return (and cache) the OCC solid body for any parametric
    ``BackendGeom`` — ``solid_geom()`` is the contract this leans
    on. ``Plate``, ``Beam``, ``PrimBox``, ``PrimCyl``, ``PrimSphere``,
    ``PrimCone``, ``PrimExtrude``, ``PrimRevolve``, ``PrimSweep``,
    ``Wall``, ``Pipe*`` all implement it.
    """
    key = occ_object.guid
    if key not in occ_solid_cache:
        occ_solid_cache[key] = geom_to_occ_geom(occ_object.solid_geom())
    return occ_solid_cache[key]


def get_shell_occ(occ_object: "BackendGeom") -> TopoDS_Shape:
    """Same for shell geometry. Requires the object to implement
    ``shell_geom()`` — currently ``Plate``, ``Beam``, ``Pipe*``,
    ``Wall``."""
    key = occ_object.guid
    if key not in occ_shell_cache:
        occ_shell_cache[key] = geom_to_occ_geom(occ_object.shell_geom())
    return occ_shell_cache[key]


def invalidate(guid: str) -> None:
    """Drop the cached solid + shell for one object. Callers that
    mutate a Beam / Plate / Prim* parametric description after the
    body has been built use this to force a rebuild on the next
    ``solid_occ()`` / ``shell_occ()`` call."""
    occ_solid_cache.pop(guid, None)
    occ_shell_cache.pop(guid, None)


def clear_all() -> None:
    """Drop the entire cache. Test isolation + long-running daemon
    pruning."""
    occ_solid_cache.clear()
    occ_shell_cache.clear()

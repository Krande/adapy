from __future__ import annotations

import weakref

import ifcopenshell

from ada.core.utils import to_real

# Per-file vertex dedupe. B-rep EXPRESS rules compare topology by INSTANCE — e.g.
# IfcEdgeLoop.IsClosed requires edge N's EdgeEnd to be the SAME IfcVertexPoint as edge N+1's
# EdgeStart — so coincident-but-duplicate vertices make an otherwise perfect shell invalid.
# The cache stores entity IDs (plain ints), never entity_instances: an entity holds a strong
# reference back to its file, which would pin the WeakKeyDictionary key forever (a leak).
_vrtx_cache: "weakref.WeakKeyDictionary[ifcopenshell.file, dict]" = weakref.WeakKeyDictionary()


def cpt(f: ifcopenshell.file, p):
    return f.create_entity("IfcCartesianPoint", to_real(p))


def vrtx(f: ifcopenshell.file, p):
    cache = _vrtx_cache.setdefault(f, {})
    key = tuple(to_real(p))
    vid = cache.get(key)
    if vid is None:
        v = f.create_entity("IfcVertexPoint", VertexGeometry=cpt(f, p))
        cache[key] = v.id()
        return v
    return f.by_id(vid)

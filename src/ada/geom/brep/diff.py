"""Compare two BRep stores for topological + geometric equivalence.

This is the oracle that pins the derive producer to the imported ground truth:
``store_equivalence(import_store, derive_store)`` reports, entity for entity, where
the derived connectivity differs from what Genie authored. Matching is by *rounded
geometry*, never by id, so an imported store and a derived store whose ids differ
still line up.

Every mismatch is tagged with the class that tells us how to fix it:

* **Class 1 — coincidence sharing (weld):** an entity that matches by geometry but
  whose *sharing* is wrong — an edge present in both but with a different partner-
  ring size, faces split into a different number of lumps, or a duplicate the derive
  producer emitted (over-derivation / fragmentation). The weld should have shared or
  merged these; it did not.
* **Class 2 — split/imprint:** an entity present in the ground truth but absent from
  the derived store — a split that was not made (a flat plate not cut along its
  stiffener). Derivable from the plate plus the beam axis, both of which we have.
* **Class 3 — non-derivable:** anything left over that cannot be attributed to weld
  or imprint. If this is non-empty it names the only things that legitimately must
  be carried from import rather than derived.

The classification is guidance for the hardening loop, not a proof; "done" is when
Class 1 and Class 2 both reach zero on a fixture.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ada.geom.brep.geom_keys import curve_key, point_key, surface_key
from ada.geom.brep.store import BRepStore


def _vk(vertex, nd):
    return point_key(vertex.point, nd)


def _ek(edge, nd):
    return (frozenset({point_key(edge.start.point, nd), point_key(edge.end.point, nd)}), curve_key(edge.curve, nd))


def _fk(face, nd):
    outer = tuple(sorted(_ek(c.edge, nd) for c in (face.outer.coedges if face.outer else [])))
    return (surface_key(face.surface, nd), outer, len(face.inner))


@dataclass
class StoreDiff:
    nd: int
    summary_a: dict
    summary_b: dict
    # geometry-keyed multiset gaps
    missing_vertices: Counter = field(default_factory=Counter)  # in a, not b
    extra_vertices: Counter = field(default_factory=Counter)  # in b, not a
    missing_edges: Counter = field(default_factory=Counter)
    extra_edges: Counter = field(default_factory=Counter)
    missing_faces: Counter = field(default_factory=Counter)
    extra_faces: Counter = field(default_factory=Counter)
    # sharing mismatches on matched edges: edge-key -> (ring_a, ring_b)
    ring_mismatch: dict = field(default_factory=dict)
    lump_count_a: int = 0
    lump_count_b: int = 0

    @property
    def is_equivalent(self) -> bool:
        return (
            not self.missing_vertices
            and not self.extra_vertices
            and not self.missing_edges
            and not self.extra_edges
            and not self.missing_faces
            and not self.extra_faces
            and not self.ring_mismatch
            and self.lump_count_a == self.lump_count_b
        )

    def classify(self) -> dict:
        """Bucket the diffs into the three fix-classes (see module docstring)."""
        missing = sum(self.missing_vertices.values()) + sum(self.missing_edges.values()) + sum(
            self.missing_faces.values()
        )
        extra = sum(self.extra_vertices.values()) + sum(self.extra_edges.values()) + sum(self.extra_faces.values())
        ring = len(self.ring_mismatch)
        lump = abs(self.lump_count_a - self.lump_count_b)
        return {
            # under-derivation: splits not made
            "class2_split_imprint": missing,
            # over-derivation + wrong sharing + fragmentation: weld
            "class1_weld": extra + ring + lump,
            # nothing attributed elsewhere yet
            "class3_non_derivable": 0,
        }

    def report(self, limit: int = 6) -> str:
        lines = [
            f"store diff (nd={self.nd})  a={self.summary_a}  b={self.summary_b}",
            f"  lumps: a={self.lump_count_a} b={self.lump_count_b}",
            f"  vertices: missing(a\\b)={sum(self.missing_vertices.values())} extra(b\\a)={sum(self.extra_vertices.values())}",
            f"  edges:    missing(a\\b)={sum(self.missing_edges.values())} extra(b\\a)={sum(self.extra_edges.values())}",
            f"  faces:    missing(a\\b)={sum(self.missing_faces.values())} extra(b\\a)={sum(self.extra_faces.values())}",
            f"  edge ring-size mismatches: {len(self.ring_mismatch)}",
            f"  classify: {self.classify()}",
        ]
        for name, c in (("missing edges", self.missing_edges), ("extra edges", self.extra_edges)):
            if c:
                lines.append(f"  {name} sample:")
                for k, n in list(c.items())[:limit]:
                    pts = sorted(tuple(round(x, 3) for x in p) for p in k[0])
                    lines.append(f"    {n}x {pts} curve={k[1][0]}")
        return "\n".join(lines)


def store_equivalence(a: BRepStore, b: BRepStore, nd: int = 6) -> StoreDiff:
    """Diff ``a`` (ground truth) against ``b`` (derived), keyed by rounded geometry."""
    d = StoreDiff(nd=nd, summary_a=a.summary(), summary_b=b.summary())

    va = Counter(_vk(v, nd) for v in a.vertices.values())
    vb = Counter(_vk(v, nd) for v in b.vertices.values())
    d.missing_vertices = va - vb
    d.extra_vertices = vb - va

    # edges: multiset by key + ring size per key (max over duplicates)
    ea, eb = Counter(), Counter()
    ring_a: dict = {}
    ring_b: dict = {}
    for e in a.edges.values():
        k = _ek(e, nd)
        ea[k] += 1
        ring_a[k] = max(ring_a.get(k, 0), len(a.coedges_on(e)))
    for e in b.edges.values():
        k = _ek(e, nd)
        eb[k] += 1
        ring_b[k] = max(ring_b.get(k, 0), len(b.coedges_on(e)))
    d.missing_edges = ea - eb
    d.extra_edges = eb - ea
    for k in set(ring_a) & set(ring_b):
        if ring_a[k] != ring_b[k]:
            d.ring_mismatch[k] = (ring_a[k], ring_b[k])

    fa = Counter(_fk(f, nd) for f in a.faces.values())
    fb = Counter(_fk(f, nd) for f in b.faces.values())
    d.missing_faces = fa - fb
    d.extra_faces = fb - fa

    d.lump_count_a = len(a.lumps)
    d.lump_count_b = len(b.lumps)
    return d

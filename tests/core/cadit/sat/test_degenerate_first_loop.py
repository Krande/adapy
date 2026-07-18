"""A SAT face whose FIRST loop is a degenerate ``hole`` — a single zero-length, curve-less coedge
marking a surface singularity (both its vertices are the same point) — with the real ``periphery``
boundary in the *next* loop of the face's loop chain.

Regression for :func:`ada.cadit.sat.read.advanced_face.get_face_bound`, which used to read only the
face's first loop. When ACIS ordered the degenerate hole first, the wire came back empty and the
whole plate failed to build (``build_advanced_face: wire build failed``), silently dropping valid
hull-skin plates from Genie-XML -> STEP/IFC audits. The fix walks ``loop.next`` and takes the outer
(periphery) loop instead.

Fixture ``degenerate_first_loop.sat`` is the minimal record closure of face ``FACE00000604`` from
Utror_6k_pl_v2005_FAL_noCage_ULST11 (28 records).
"""

import ada.geom.curves as geo_cu
import ada.geom.surfaces as geo_su
from ada.cadit.sat.read.advanced_face import get_face_bound, get_face_surface
from ada.cadit.sat.read.curves import iter_loop_coedges
from ada.cadit.sat.store import SatReaderFactory

FIXTURE = "sat_files/degenerate_first_loop.sat"


def _face(example_files):
    saf = SatReaderFactory(str(example_files / FIXTURE))
    saf.load_sat_data_from_file()
    faces = [r for r in saf.sat_store.iter() if r.type == "face"]
    assert len(faces) == 1, "fixture should carry exactly one face"
    return saf, faces[0]


def test_first_loop_is_a_degenerate_hole(example_files):
    """The bug's precondition: the face's FIRST loop is a hole that yields no usable edges."""
    saf, face = _face(example_files)
    first_loop = saf.sat_store.get(face.chunks[7])
    assert first_loop.chunks[16] == "hole"
    # iter_loop_coedges correctly steps over the single degenerate coedge -> empty.
    assert list(iter_loop_coedges(first_loop)) == []


def test_get_face_bound_follows_the_loop_chain_to_the_periphery(example_files):
    """The fix: get_face_bound walks past the degenerate hole to the real periphery boundary."""
    saf, face = _face(example_files)
    bounds = get_face_bound(face)
    assert len(bounds) == 1
    edge_list = bounds[0].bound.edge_list
    # The real rectangular plate boundary — 4 edges — not the empty hole loop.
    assert len(edge_list) == 4
    assert all(isinstance(e, geo_cu.OrientedEdge) for e in edge_list)
    # The face itself is planar; the periphery bounds it.
    assert isinstance(get_face_surface(face), geo_su.Plane)

import itertools
from typing import TYPE_CHECKING

import numpy as np

from ada import CurvePoly2d, Placement
from ada.api.transforms import to_global_points, to_global_vectors

if TYPE_CHECKING:
    from ada import Beam

    from ..concepts import GmshData, GmshSession


def ibeam(model: "GmshData", gmsh_session: "GmshSession"):
    # pass
    bm_obj: "Beam" = model.obj
    for cut in make_ig_cutplanes(bm_obj):
        gmsh_session.add_cutting_plane(cut, [model])

    gmsh_session.make_cuts()

    for dim, tag in gmsh_session.model.get_entities():
        if dim == 2:
            gmsh_session.model.mesh.set_transfinite_surface(tag)
            gmsh_session.model.mesh.setRecombine(dim, tag)

    for dim, tag in gmsh_session.model.get_entities():
        if dim == 3:
            gmsh_session.model.mesh.set_transfinite_volume(tag)
            gmsh_session.model.mesh.setRecombine(dim, tag)

    # gmsh_session.open_gui()

    # raise NotImplementedError()


def make_ig_cutplanes(bm: "Beam"):
    from ..concepts import CutPlane

    # The cut planes act on the global gmsh model, so the beam is taken to the global system first. Note that the
    # cut planes are axis-aligned, so this only partitions beams that are axis-aligned in the global system.
    n1 = to_global_points(bm, bm.n1.p)
    yvec, up = to_global_vectors(bm, [bm.yvec, bm.up])

    points2d = bm.section.get_section_profile().outer_curve.points2d
    sec_place = Placement(n1, yvec, up)
    points3d = sec_place.transform_local_points_back_to_global(points2d)

    minz = min([x[2] for x in points3d])
    maxz = max([x[2] for x in points3d])
    bbox = bm.bbox()
    corners = to_global_points(bm, list(itertools.product(*zip(bbox.p1, bbox.p2))))
    pmin, pmax = corners.min(axis=0), corners.max(axis=0)
    dx, dy, dz = (np.array(pmax) - np.array(pmin)) * 1.0
    x, y, _ = pmin

    sec = bm.section

    cut1 = CutPlane((x, y, minz + sec.t_fbtn), dx=dx, dy=dy)
    cut2 = CutPlane((x, y, maxz - sec.t_fbtn), dx=dx, dy=dy)

    web_left = n1 - (sec.t_w / 2) * yvec - (sec.h / 2) * up
    web_right = n1 + (sec.t_w / 2) * yvec - (sec.h / 2) * up
    dy = sec.h
    cut3 = CutPlane(web_left, dx=dx, dy=dy, plane="XZ")
    cut4 = CutPlane(web_right, dx=dx, dy=dy, plane="XZ")

    return [cut1, cut2, cut3, cut4]


def get_bm_section_curve(bm: "Beam", origin=None) -> CurvePoly2d:
    origin = origin if origin is not None else bm.n1.p
    section_profile = bm.section.get_section_profile(True)
    points2d = section_profile.outer_curve.points2d
    return CurvePoly2d(points2d=points2d, origin=origin, xdir=bm.yvec, normal=bm.xvec, parent=bm.parent)

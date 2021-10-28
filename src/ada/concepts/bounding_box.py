from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Tuple, Union

import numpy as np

from .transforms import Placement

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from .points import Node
    from .primitives import PrimBox
    from .structural import Beam


@dataclass
class BoundingBox:
    parent: Union[PrimBox, Beam]
    placement: Placement = None
    sides: BoxSides = None
    p1: np.array = None
    p2: np.array = None

    def __post_init__(self):
        from .primitives import PrimBox
        from .structural import Beam

        if type(self.parent) is PrimBox:
            self.p1, self.p2 = self.parent.p1, self.parent.p2
            self.placement = self.parent.placement
        elif type(self.parent) is Beam:
            self.p1, self.p2 = self._calc_bbox_of_beam()
            # TODO
            self.placement = self.parent.placement
        else:
            raise NotImplementedError(f'Bounding Box Support for object type "{type(self.parent)}" is not yet added')
        self.sides = BoxSides(self)

    def _calc_bbox_of_beam(self) -> Tuple[tuple, tuple]:
        """Get the bounding box of a beam"""
        from itertools import chain

        from ada import Beam, Section
        from ada.core.utils import roundoff

        from ..sections import SectionCat

        bm = self.parent
        if SectionCat.is_circular_profile(bm.section.type) or SectionCat.is_tubular_profile(bm.section.type):
            d = bm.section.r * 2
            dummy_beam = Beam("dummy", bm.n1.p, bm.n2.p, Section("DummySec", "BG", h=d, w_btn=d, w_top=d))
            outer_curve = dummy_beam.get_outer_points()
        else:
            outer_curve = bm.get_outer_points()

        points = np.array(list(chain.from_iterable(outer_curve)))
        xv = sorted([roundoff(p[0]) for p in points])
        yv = sorted([roundoff(p[1]) for p in points])
        zv = sorted([roundoff(p[2]) for p in points])
        xmin, xmax = xv[0], xv[-1]
        ymin, ymax = yv[0], yv[-1]
        zmin, zmax = zv[0], zv[-1]
        return (xmin, ymin, zmin), (xmax, ymax, zmax)

    def build_bbox_using_occ_shape(self, shape: TopoDS_Shape, tol=1e-6, use_mesh=True):
        """

        :param shape: TopoDS_Shape or a subclass such as TopoDS_Face the shape to compute the bounding box from
        :param tol: tolerance of the computed boundingbox
        :param use_mesh: a flag that tells whether or not the shape has first to be meshed before the bbox computation.
                         This produces more accurate results
        :return: return the bounding box of the TopoDS_Shape `shape`
        """

        bbox = Bnd_Box()
        bbox.SetGap(tol)
        if use_mesh:
            mesh = BRepMesh_IncrementalMesh()
            mesh.SetParallel(True)
            mesh.SetShape(shape)
            mesh.Perform()
            if not mesh.IsDone():
                raise AssertionError("Mesh not done.")
        brepbndlib_Add(shape, bbox, use_mesh)

        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        return xmin, ymin, zmin, xmax, ymax, zmax, xmax - xmin, ymax - ymin, zmax - zmin

    @property
    def minmax(self):
        return self.p1, self.p2


@dataclass
class BoxSides:
    parent: BoundingBox

    def _return_fem_nodes(self, pmin, pmax, fem=None):
        part = self.parent.parent.parent
        if fem is None and self.parent is not None and part.fem.is_empty() is False:
            fem = part.fem

        if fem is None:
            raise ValueError("No FEM data found. Cannot return FEM nodes")

        return fem.nodes.get_by_volume(p=pmin, vol_box=pmax)

    def _return_data(self, pmin, pmax, fem, return_fem_nodes) -> Union[Tuple[tuple, tuple], List[Node]]:
        if return_fem_nodes is True:
            return self._return_fem_nodes(pmin, pmax, fem)

        return pmin, pmax

    def _return_surface(self):
        pass

    def _get_dim(self):
        box = self.parent
        p1 = np.array(box.p1)
        p2 = np.array(box.p2)
        l, w, h = p2 - p1
        return l, w, h, p1, p2

    def top(self, tol=1e-3, return_fem_nodes=False, fem=None):
        """Top is at positive local Z"""
        l, w, h, p1, p2 = self._get_dim()

        z = self.parent.placement.zdir

        pmin = p1 + l * z - tol
        pmax = p2 + tol

        return self._return_data(pmin, pmax, fem, return_fem_nodes)

    def bottom(self, tol=1e-3, return_fem_nodes=False, fem=None):
        """Bottom is at negative local z"""
        l, w, h, p1, p2 = self._get_dim()

        z = self.parent.placement.zdir

        pmin = p1 - tol
        pmax = p2 - l * z + tol

        return self._return_data(pmin, pmax, fem, return_fem_nodes)

    def front(self, tol=1e-3, return_fem_nodes=False, fem=None):
        """Front is at positive local y"""
        l, w, h, p1, p2 = self._get_dim()

        y = self.parent.placement.ydir

        pmin = p1 + l * y - tol
        pmax = p2 + tol

        return self._return_data(pmin, pmax, fem, return_fem_nodes)

    def back(self, tol=1e-3, return_fem_nodes=False, fem=None):
        """Back is at negative local y"""
        l, w, h, p1, p2 = self._get_dim()

        y = self.parent.placement.ydir

        pmin = p1 - tol
        pmax = p2 - l * y + tol

        return self._return_data(pmin, pmax, fem, return_fem_nodes)

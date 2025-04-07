from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import numpy as np

from ada.config import logger

from .transforms import Placement

if TYPE_CHECKING:
    from ada import FEM, Node
    from ada.api.beams import Beam
    from ada.api.plates import Plate
    from ada.fem import Surface

    from .primitives import PrimBox


@dataclass
class BoundingBox:
    parent: PrimBox | Beam | Plate
    placement: Placement = field(default=None, init=False)
    sides: BoxSides = field(default=None, init=False)
    p1: np.array = field(default=None, init=False)
    p2: np.array = field(default=None, init=False)

    def __post_init__(self):
        from ada.api.beams import Beam
        from ada.api.plates import Plate

        from .primitives import Shape

        if issubclass(type(self.parent), Shape):
            self.p1, self.p2 = self._calc_bbox_of_shape()
            self.placement = self.parent.placement
        elif isinstance(self.parent, Beam):
            self.p1, self.p2 = self._calc_bbox_of_beam()
            self.placement = Placement(
                self.parent.placement.origin, xdir=self.parent.yvec, ydir=self.parent.xvec, zdir=self.parent.up
            )
        elif isinstance(self.parent, Plate):
            self.p1, self.p2 = self._calc_bbox_of_plate()
            self.placement = self.parent.placement
        else:
            raise NotImplementedError(f'Bounding Box Support for object type "{type(self.parent)}" is not yet added')

        self.sides = BoxSides(self)

    def _calc_bbox_of_beam(self) -> tuple[tuple, tuple]:
        """Get the bounding box of a beam"""
        from itertools import chain

        from ada import Beam, Section
        from ada.core.utils import roundoff
        from ada.sections.categories import BaseTypes

        bm = self.parent
        if bm.section.type == BaseTypes.CIRCULAR or bm.section.type == BaseTypes.TUBULAR:
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

    def _calc_bbox_of_shape(self) -> tuple[tuple, tuple]:
        from .exceptions import NoGeomPassedToShapeError
        from .primitives import PrimBox

        if type(self.parent) is PrimBox:
            return self.parent.p1, self.parent.p2
        else:
            from ada.occ.utils import get_boundingbox

            try:
                return get_boundingbox(self.parent.solid_occ(), use_mesh=True)
            except NoGeomPassedToShapeError as e:
                logger.info(f'Shape "{self.parent.name}" has no attached geometry. Error "{e}"')
                return (0, 0, 0), (1, 1, 1)

    def _calc_bbox_of_plate(self) -> tuple[tuple, tuple]:
        """Calculate the Bounding Box of a plate"""
        plate: Plate = self.parent
        p3d = plate.placement.get_absolute_placement().origin + np.asarray(plate.poly.points3d)
        bbox_min = p3d.min(axis=0)
        bbox_max = p3d.max(axis=0)
        n = plate.poly.normal.astype(np.float64)

        pv = np.nonzero(n)[0]
        matr = {0: "X", 1: "Y", 2: "Z"}
        orient = matr[pv[0]]
        if orient == "X" or orient == "Y":
            delta_vec = abs(n * plate.t / 2.0)
            bbox_min -= delta_vec
            bbox_max += delta_vec
        elif orient == "Z":
            delta_vec = abs(n * plate.t).astype(np.float64)
            bbox_min -= delta_vec
        else:
            raise ValueError(f"Error in {orient}")

        return tuple(bbox_min), tuple(bbox_max)

    @property
    def minmax(self):
        return self.p1, self.p2

    @property
    def volume_cog(self):
        """Get volumetric COG from bounding box"""

        return np.array(
            [
                (self.p1[0] + self.p2[0]) / 2,
                (self.p1[1] + self.p2[1]) / 2,
                (self.p1[2] + self.p2[2]) / 2,
            ]
        )


@dataclass
class BoxSides:
    parent: BoundingBox

    def _return_fem_nodes(self, pmin, pmax, fem):
        return fem.nodes.get_by_volume(p=pmin, vol_box=pmax)

    def _return_data(
        self, pmin, pmax, fem, return_fem_nodes, return_surface, surface_name, shell_positive
    ) -> tuple[tuple, tuple] | list[Node] | Surface:
        if return_fem_nodes is True or return_surface is True:
            part = self.parent.parent.parent
            if fem is None and self.parent is not None and part.fem.is_empty() is False:
                fem = part.fem

            if fem is None:
                raise ValueError("No FEM data found. Cannot return FEM nodes")

        if return_fem_nodes is True:
            return self._return_fem_nodes(pmin, pmax, fem)

        if return_surface is True:
            if surface_name is None:
                from .exceptions import NameIsNoneError

                raise NameIsNoneError("You must give 'surface_name' a string name unequal to None")
            nodes = self._return_fem_nodes(pmin, pmax, fem)
            if len(nodes) == 0:
                raise ValueError(f"Zero nodes found for (pmin, pmax): ({pmin}, {pmax})")
            return self._return_surface(surface_name, nodes, fem, shell_positive)
        return pmin, pmax

    def _return_surface(self, surface_name: str, nodes: list[Node], fem: FEM, shell_positive):
        from ada.fem.surfaces import create_surface_from_nodes

        return create_surface_from_nodes(surface_name, nodes, fem, shell_positive)

    def _get_dim(self):
        from ada import Beam

        bbox = self.parent
        p1 = np.array(bbox.p1)
        p2 = np.array(bbox.p2)

        bounded_obj = bbox.parent

        if type(bounded_obj) is Beam:
            l = bounded_obj.length
            w = max(bounded_obj.section.w_btn, bounded_obj.section.w_top)
            h = bounded_obj.section.h
        else:
            l, w, h = p2 - p1

        return l, w, h, p1, p2

    def _side(
        self,
        axis: np.ndarray,
        length: float,
        positive: bool,
        tol: float,
        return_fem_nodes: bool,
        fem,
        return_surface: bool,
        surface_name: str | None,
        surf_positive: bool,
    ):
        l, w, h, p1, p2 = self._get_dim()
        direction = axis * length
        if positive:
            pmin = p1 + direction - tol
            pmax = p2 + tol
        else:
            pmin = p1 - tol
            pmax = p2 - direction + tol

        return self._return_data(pmin, pmax, fem, return_fem_nodes, return_surface, surface_name, surf_positive)

    def top(
        self,
        tol: float = 1e-3,
        return_fem_nodes: bool = False,
        fem=None,
        return_surface: bool = False,
        surface_name: str | None = None,
        surf_positive: bool = False,
    ):
        return self._side(
            axis=self.parent.placement.zdir,
            length=self._get_dim()[2],
            positive=True,
            tol=tol,
            return_fem_nodes=return_fem_nodes,
            fem=fem,
            return_surface=return_surface,
            surface_name=surface_name,
            surf_positive=surf_positive,
        )

    def get(
        self,
        sides: list[Literal["front", "back", "top", "bottom"]],
        tol: float = 1e-3,
        return_fem_nodes: bool = False,
        fem=None,
        return_surface: bool = False,
        surface_name: str | None = None,
        surf_positive: bool = False,
    ):
        """Get the side of the bounding box"""

        sides_dict = {
            "top": self.top,
            "bottom": self.bottom,
            "front": self.front,
            "back": self.back,
        }

        results = []
        for side in sides:
            if side in sides_dict:
                results.extend(
                    sides_dict[side](tol, return_fem_nodes, fem, return_surface, surface_name, surf_positive)
                )
            else:
                raise ValueError(f"Invalid side: {side}. Valid sides are: {list(sides_dict.keys())}")

        return results

    def bottom(
        self,
        tol: float = 1e-3,
        return_fem_nodes: bool = False,
        fem=None,
        return_surface: bool = False,
        surface_name: str | None = None,
        surf_positive: bool = False,
    ):
        return self._side(
            axis=self.parent.placement.zdir,
            length=self._get_dim()[2],
            positive=False,
            tol=tol,
            return_fem_nodes=return_fem_nodes,
            fem=fem,
            return_surface=return_surface,
            surface_name=surface_name,
            surf_positive=surf_positive,
        )

    def front(
        self,
        tol: float = 1e-3,
        return_fem_nodes: bool = False,
        fem=None,
        return_surface: bool = False,
        surface_name: str | None = None,
        surf_positive: bool = False,
    ):
        return self._side(
            axis=self.parent.placement.ydir,
            length=self._get_dim()[0],
            positive=True,
            tol=tol,
            return_fem_nodes=return_fem_nodes,
            fem=fem,
            return_surface=return_surface,
            surface_name=surface_name,
            surf_positive=surf_positive,
        )

    def back(
        self,
        tol: float = 1e-3,
        return_fem_nodes: bool = False,
        fem=None,
        return_surface: bool = False,
        surface_name: str | None = None,
        surf_positive: bool = False,
    ):
        return self._side(
            axis=self.parent.placement.ydir,
            length=self._get_dim()[0],
            positive=False,
            tol=tol,
            return_fem_nodes=return_fem_nodes,
            fem=fem,
            return_surface=return_surface,
            surface_name=surface_name,
            surf_positive=surf_positive,
        )

    def all_sides(
        self,
        tol: float = 1e-3,
        return_fem_nodes: bool = False,
        fem=None,
        return_surface: bool = False,
        surface_name: str | None = None,
        surf_positive: bool = False,
    ):
        return (
            self.top(tol, return_fem_nodes, fem, return_surface, surface_name, surf_positive),
            self.bottom(tol, return_fem_nodes, fem, return_surface, surface_name, surf_positive),
            self.front(tol, return_fem_nodes, fem, return_surface, surface_name, surf_positive),
            self.back(tol, return_fem_nodes, fem, return_surface, surface_name, surf_positive),
        )

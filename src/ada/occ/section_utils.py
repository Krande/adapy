from typing import Union

import numpy as np
from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Wire
from OCC.Extend.ShapeFactory import make_face, make_wire

from ada.concepts.transforms import Placement
from ada.sections.categories import BaseTypes
from ada.sections.concept import SectionProfile

from .utils import make_circle, make_face_w_cutout


def cross_sec_face(sec_profile: SectionProfile, placement: Placement, solid_repre) -> Union[TopoDS_Face, TopoDS_Wire]:
    inner_shape = None

    if sec_profile.sec.type == BaseTypes.TUBULAR:
        outer_shape = make_wire([make_circle(placement.origin, placement.zdir, sec_profile.sec.r)])
        inner_shape = make_wire([make_circle(placement.origin, placement.zdir, sec_profile.sec.r - sec_profile.sec.wt)])
    elif sec_profile.sec.type == BaseTypes.CIRCULAR:
        outer_shape = make_wire([make_circle(placement.origin, placement.zdir, sec_profile.sec.r)])
    elif sec_profile.sec.type == BaseTypes.GENERAL:
        radius = np.sqrt(sec_profile.sec.properties.Ax / np.pi)
        outer_shape = make_wire([make_circle(placement.origin, placement.zdir, radius)])
    else:
        if sec_profile.disconnected is False:
            outer_curve = sec_profile.outer_curve
            inner_curve = sec_profile.inner_curve
            outer_curve.placement = placement
            outer_shape = outer_curve.face
            if inner_curve is not None:
                inner_curve.placement = placement
                inner_shape = inner_curve.wire
        else:
            if sec_profile.outer_curve_disconnected is None:
                raise ValueError(f"Section profile {sec_profile.sec.name} has no Curve associated with it")

            outer_shape = []
            for curve in sec_profile.outer_curve_disconnected:
                curve.placement = placement
                outer_shape.append(curve.wire)

    if inner_shape is not None and solid_repre is True:
        try:
            face = make_face(outer_shape) if type(outer_shape) is not TopoDS_Face else outer_shape
            shape = make_face_w_cutout(face, inner_shape)
        except TypeError as e:
            raise TypeError(e)
        except AssertionError as e:
            raise AssertionError(e)
    else:
        shape = outer_shape
    if shape is None:
        raise ValueError("Shape cannot be None")
    return shape

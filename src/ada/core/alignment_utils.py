from typing import TYPE_CHECKING

import numpy as np

from .vector_utils import vector_length

if TYPE_CHECKING:
    from ada import Beam, Plate


def align_to_plate(plate: "Plate"):
    normal = plate.poly.normal
    h = plate.t * 5
    origin = plate.poly.origin - h * normal * 1.1 / 2
    xdir = plate.poly.xdir
    return dict(h=h, normal=normal, origin=origin, xdir=xdir)


def align_to_beam(beam: "Beam"):
    ymin = beam.yvec * np.array(beam.bbox().p1)
    ymax = beam.yvec * np.array(beam.bbox().p2)
    origin = beam.n1.p - ymin * 1.1
    normal = -beam.yvec
    xdir = beam.xvec
    h = vector_length(ymax - ymin) * 1.2
    return dict(h=h, normal=normal, origin=origin, xdir=xdir)

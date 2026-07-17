from __future__ import annotations

# NOTE: this module imports adapy's API surface eagerly, but only pure-python
# deps at module level (numpy/pyquaternion/trimesh). The CAD/FEM kernels
# (pythonocc-core / gmsh / ifcopenshell) are pulled lazily at call time via the
# CadBackend abstraction, so `import ada` loads under pyodide/wasm too — the
# pyodide wheel (deploy/Dockerfile.viewer) ships this file as-is. Genuinely
# native *operations* fail at call time with a clear error where no wasm
# backend exists.
from ada import fem
from ada._version import __version__  # noqa: F401 — re-exported as ada.__version__
from ada.api.beams import (
    Beam,
    BeamCurved,
    BeamHinge,
    BeamHingeDofType,
    BeamRevolve,
    BeamSweep,
    BeamTapered,
)
from ada.api.boolean import Boolean
from ada.api.connections import Connection
from ada.api.curves import ArcSegment, CurvePoly2d, CurveRevolve, LineSegment
from ada.api.fasteners import Bolts, IntermittentSpec, Weld, WeldType
from ada.api.groups import Group
from ada.api.mass import MassPoint
from ada.api.nodes import Node
from ada.api.piping import Pipe, PipeSegElbow, PipeSegStraight
from ada.api.plates import Plate, PlateCurved, Surface, SurfaceCurved
from ada.api.primitives import (
    PrimBox,
    PrimCone,
    PrimCyl,
    PrimExtrude,
    PrimRevolve,
    PrimSphere,
    PrimSweep,
    Shape,
)
from ada.api.primitives.bool_half_space import BoolHalfSpace
from ada.api.spatial import Assembly, Part
from ada.api.spatial.equipment import Equipment
from ada.api.transforms import Instance, Placement, Transform
from ada.api.user import User
from ada.api.walls import Wall
from ada.base.units import Units
from ada.config import configure_logger, logger
from ada.core.utils import Counter
from ada.deprecation import deprecated
from ada.fem import FEM
from ada.fem.concept.constraints import (
    ConstraintConceptCurve,
    ConstraintConceptDofType,
    ConstraintConceptPoint,
    ConstraintConceptRigidLink,
    RigidLinkRegion,
)
from ada.fem.concept.loads import (
    LoadConceptAccelerationField,
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptLine,
    LoadConceptPoint,
    LoadConceptSurface,
    RotationalAccelerationField,
)
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.materials import Material
from ada.sections import Section
from ada.visit.config import set_jupyter_part_renderer

__author__ = "Kristoffer H. Andersen"

# A set of convenience name generators for plates and beams
PL_N = Counter(start=1, prefix="PL")
BM_N = Counter(start=1, prefix="BM")
configure_logger()


from ada.factories import (  # noqa: E402 - imported after the API symbols the factories use
    from_acis,
    from_fem,
    from_fem_res,
    from_genie_xml,
    from_ifc,
    from_pickle,
    from_sesam_cc,
    from_step,
    iter_from_step,
)

__all__ = [
    "Assembly",
    "Part",
    "Connection",
    "FEM",
    "from_ifc",
    "from_fem",
    "from_step",
    "iter_from_step",
    "from_sesam_cc",
    "from_acis",
    "from_pickle",
    "from_genie_xml",
    "from_fem_res",
    "logger",
    "Beam",
    "BeamTapered",
    "BeamSweep",
    "BeamRevolve",
    "BeamCurved",
    "BeamHinge",
    "BeamHingeDofType",
    "Boolean",
    "Counter",
    "deprecated",
    "Equipment",
    "Group",
    "BoolHalfSpace",
    "ConstraintConceptPoint",
    "ConstraintConceptCurve",
    "ConstraintConceptRigidLink",
    "ConstraintConceptDofType",
    "RigidLinkRegion",
    "RotationalAccelerationField",
    "LoadConceptCase",
    "LoadConceptPoint",
    "LoadConceptLine",
    "LoadConceptSurface",
    "LoadConceptAccelerationField",
    "LoadConceptCaseCombination",
    "LoadConceptCaseFactored",
    "MassPoint",
    "Plate",
    "PlateCurved",
    "Surface",
    "SurfaceCurved",
    "Pipe",
    "PipeSegStraight",
    "PipeSegElbow",
    "Wall",
    "Section",
    "Material",
    "Shape",
    "Node",
    "Point",
    "Direction",
    "Placement",
    "PrimBox",
    "PrimCone",
    "PrimCyl",
    "PrimExtrude",
    "PrimRevolve",
    "PrimSphere",
    "PrimSweep",
    "CurvePoly2d",
    "CurveRevolve",
    "LineSegment",
    "ArcSegment",
    "Transform",
    "Instance",
    "User",
    "Bolts",
    "IntermittentSpec",
    "Weld",
    "WeldType",
    "Units",
    "fem",
    "set_jupyter_part_renderer",
    "BM_N",
    "PL_N",
]


def _jupyter_nbextension_paths():
    return [
        {
            "section": "notebook",
            "src": "ada/_static",  # relative to this package
            "dest": "adapy",  # becomes /nbextensions/adapy/
            "require": "adapy/main",  # if your main bundle is main.js
        }
    ]


def _jupyter_labextension_paths():
    return [{"src": "ada/_static", "dest": "adapy"}]  # relative to this package  # labextensions/adapy

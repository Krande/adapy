"""Abaqus/CAE output: a script that rebuilds a concept model as editable geometry.

Entry point: :meth:`ada.Part.to_abaqus_cae_script`, which delegates to
:func:`ada.cadit.cae.writer.write_cae_script`.
"""

from .curves import (
    CURVE_LENGTH_REL_TOL,
    MAX_TURN_RADIANS,
    CurveNotSupported,
    is_curved_beam_type,
    sample_member_curve,
)
from .names import CaeNameError, NameRegistry, sanitise_cae_name
from .topology import (
    CAE_MERGE_TOL,
    Crossing,
    PartTopology,
    Segment,
    expected_topology,
    find_crossings,
)
from .writer import (
    CURVE_N1_MIN_SIN,
    CURVED_BEAM_TYPES,
    REFUSED_BEAM_TYPES,
    CaeWriteError,
    SkippedObject,
    UnsupportedBeamError,
    beam_endpoints,
    beam_n1,
    beam_section_offset,
    build_plan,
    check_beam_shape,
    check_n1_holds_along_the_curve,
    check_unit_scale,
    render_script,
    write_cae_script,
)

__all__ = [
    "CAE_MERGE_TOL",
    "CURVED_BEAM_TYPES",
    "CURVE_LENGTH_REL_TOL",
    "CURVE_N1_MIN_SIN",
    "MAX_TURN_RADIANS",
    "REFUSED_BEAM_TYPES",
    "CaeNameError",
    "CaeWriteError",
    "Crossing",
    "CurveNotSupported",
    "NameRegistry",
    "PartTopology",
    "Segment",
    "SkippedObject",
    "UnsupportedBeamError",
    "beam_endpoints",
    "beam_n1",
    "beam_section_offset",
    "build_plan",
    "check_beam_shape",
    "check_n1_holds_along_the_curve",
    "check_unit_scale",
    "expected_topology",
    "find_crossings",
    "is_curved_beam_type",
    "render_script",
    "sample_member_curve",
    "sanitise_cae_name",
    "write_cae_script",
]

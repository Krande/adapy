"""Abaqus/CAE output: a script that rebuilds a concept model as editable geometry.

Entry point: :meth:`ada.Part.to_abaqus_cae_script`, which delegates to
:func:`ada.cadit.cae.writer.write_cae_script`.
"""

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
    CaeWriteError,
    SkippedObject,
    UnsupportedBeamError,
    beam_endpoints,
    beam_n1,
    build_plan,
    check_beam_has_no_eccentricity,
    check_beam_is_straight,
    check_unit_scale,
    render_script,
    write_cae_script,
)

__all__ = [
    "CAE_MERGE_TOL",
    "CaeNameError",
    "CaeWriteError",
    "Crossing",
    "NameRegistry",
    "PartTopology",
    "Segment",
    "SkippedObject",
    "UnsupportedBeamError",
    "beam_endpoints",
    "beam_n1",
    "build_plan",
    "check_beam_has_no_eccentricity",
    "check_beam_is_straight",
    "check_unit_scale",
    "expected_topology",
    "find_crossings",
    "render_script",
    "sanitise_cae_name",
    "write_cae_script",
]

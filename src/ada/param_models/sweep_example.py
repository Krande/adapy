"""Back-compat shim: the OCC pipe-shell sweep demo moved to
:mod:`ada.occ.sweep_example`.

It is an independent pythonocc reference (the swept-area tests cross-check
``ada.PrimSweep`` against it), so it now lives under ``ada.occ`` to keep all OCC
calls confined there. This module re-exports the public names so the historical
import path ``ada.param_models.sweep_example`` keeps working. The data tables and
the module re-export carry no OCC import; the OCC kernel is only touched when the
construction helpers are actually called.
"""

from ada.occ.sweep_example import (
    adapy_viewer,
    build_three_sweeps,
    fillet,
    get_three_sweeps_mesh_data,
    make_profile_wire,
    make_wire_from_points,
    sweep1_pts,
    sweep2_pts,
    sweep3_pts,
    sweep_profile_along_path,
    wt,
)

__all__ = [
    "adapy_viewer",
    "build_three_sweeps",
    "fillet",
    "get_three_sweeps_mesh_data",
    "make_profile_wire",
    "make_wire_from_points",
    "sweep1_pts",
    "sweep2_pts",
    "sweep3_pts",
    "sweep_profile_along_path",
    "wt",
]

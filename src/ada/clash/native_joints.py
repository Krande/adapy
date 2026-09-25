"""Beam-to-beam joint finding in adacpp, which is where this arithmetic now lives.

ONE IMPLEMENTATION, TWO RUNTIMES. The same question -- which members meet, and where -- is asked
by a worker and by a browser, and the answer has to be the same one: a joint's id is a hash of its
member names and the pass that found it, and a `clash_detail` hand-off re-derives joints from
those ids. If the browser computed them differently it would show a user a joint no worker could
then detail. So the pass is compiled once in adacpp (`clash_joints.h`) and called from both --
from here through nanobind, from the browser through embind.

It is also simply faster: the Python pass builds a `Nodes` container and a `JointBase` per contact
and walks every candidate pair in the interpreter, which on a frame with tens of thousands of
beams is the whole cost of a check.

FALLBACK, NOT REQUIREMENT. An environment without adacpp keeps the original `Connections.find`
path (`available()` is false and `identify` never calls here), so nothing that worked stops
working -- it is one release slower, not broken.
"""

from __future__ import annotations

from typing import Any, Sequence

from ada.config import logger

__all__ = ["available", "find_beam_joints"]


def available() -> bool:
    """Whether the installed adacpp carries the compiled joint pass."""
    try:
        import adacpp.cad  # noqa: PLC0415 - probing an optional backend

        return hasattr(adacpp.cad, "find_beam_joints")
    except Exception:  # noqa: BLE001 - an adacpp that cannot import is one that cannot answer
        return False


def _reach(beam) -> float:
    """Half the largest section dimension -- how far the member's solid reaches from its axis.

    Only pads the candidate box, never decides a joint, so a section whose dimensions cannot be
    read costs a tighter filter rather than a wrong answer.
    """
    try:
        sec = beam.section
        dims = [getattr(sec, name, None) for name in ("h", "w_top", "w_btn", "r")]
        values = [float(d) for d in dims if d]
        return max(values) / 2.0 if values else 0.0
    except Exception:  # noqa: BLE001 - see the docstring
        return 0.0


def _family(beam) -> str:
    sec_type = getattr(getattr(beam, "section", None), "type", None)
    value = getattr(sec_type, "value", None)
    return str(value).upper() if value is not None else ""


def find_beam_joints(beams: Sequence[Any], out_of_plane_tol: float, point_tol: float) -> list[dict]:
    """The compiled pass, over adapy `Beam` objects.

    Returns one dict per joint with the member INDICES into ``beams`` (not the objects: the caller
    owns those and the boundary should not try to), the contact centre and the angle.
    """
    import adacpp.cad  # noqa: PLC0415 - guarded by available()

    rows = [
        (
            str(getattr(bm, "name", "") or ""),
            str(getattr(bm, "guid", "") or ""),
            tuple(float(v) for v in bm.n1.p[:3]),
            tuple(float(v) for v in bm.n2.p[:3]),
            _reach(bm),
            _family(bm),
        )
        for bm in beams
    ]
    logger.debug(f"clash: native beam pass over {len(rows)} beams")
    return adacpp.cad.find_beam_joints(rows, out_of_plane_tol=out_of_plane_tol, point_tol=point_tol)

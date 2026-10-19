"""The numbers GeniE V9.2-01 writes as GECCEN for a flush or offset beam (issue #419).

GeniE measures a beam's eccentricity from the node to the section **centroid**, along the local z
(up) axis. Measured headless against ``T1.FEM``:

=================  ===========  =============
section            flush_top    flush_bottom
=================  ===========  =============
HP220x10           -0.1354302   +0.0845698
TG300x500x20x32    -0.0536405   +0.2463596
IPE300             -0.15        +0.15
=================  ===========  =============

``no_flush`` writes no GECCEN: the centroid sits on the node line. These tests pin the helper layer
(:meth:`OffsetHelper.curve_offset_local` and :func:`resolve_justification`) to those numbers; the
meshing and the Sesam deck are tested next to their own code.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.beams.justification import Justification, resolve_justification

#: section -> (flush_top, flush_bottom): the GECCEN z GeniE wrote, in metres
GENIE_FLUSH_Z = {
    "HP220x10": (-0.1354302, 0.0845698),
    "TG300x500x20x32": (-0.0536405, 0.2463596),
    "IPE300": (-0.15, 0.15),
}


def _beam(section: str, justification: Justification) -> ada.Beam:
    return ada.Beam("bm", (0, 0, 0), (4, 0, 0), section, up=(0, 0, 1), justification=justification)


@pytest.mark.parametrize("section", sorted(GENIE_FLUSH_Z))
@pytest.mark.parametrize("which, just", [(0, Justification.FLUSH_TOP), (1, Justification.FLUSH_BOTTOM)])
def test_curve_offset_local_matches_genie_for_flush(section, which, just):
    """The HP flush_bottom row is the sign error the issue names: it came out -0.35543."""
    data = _beam(section, just).offset_helper.curve_offset_local()

    for end in (data.end1, data.end2):
        assert end[0] == pytest.approx(0.0, abs=1e-12)
        assert end[1] == pytest.approx(0.0, abs=1e-12)
        assert end[2] == pytest.approx(GENIE_FLUSH_Z[section][which], abs=1e-6)


@pytest.mark.parametrize("section", sorted(GENIE_FLUSH_Z))
@pytest.mark.parametrize("which, just", [(0, Justification.FLUSH_TOP), (1, Justification.FLUSH_BOTTOM)])
def test_resolve_justification_plus_centroid_correction_matches_genie(section, which, just):
    """The issue says this pairing already matches GeniE for every flush case tested (confirmed).

    ``resolve_justification`` is the origin shift ``e``; the offset to the centroid is ``-e`` plus
    the section's centroid-from-origin, which is ``Cgz - h`` for an angle and ``Cgz - h/2`` for a T.
    """
    bm = _beam(section, just)
    e = np.asarray(resolve_justification(bm, just), dtype=float)
    sec = bm.section
    if sec.type == sec.TYPES.ANGULAR:
        add = sec.properties.Cgz - sec.h
    elif sec.type == sec.TYPES.TPROFILE:
        add = sec.properties.Cgz - sec.h / 2
    else:
        add = 0.0

    assert (-e[2] + add) == pytest.approx(GENIE_FLUSH_Z[section][which], abs=1e-6)


def test_the_hp_flush_bottom_origin_shift_is_minus_h():
    """An angle's profile hangs below its origin (z in [-h, 0]); its bottom is on the node when the
    origin is h above it, i.e. ``e = -h * up`` (``Beam.e`` is node minus origin)."""
    bm = _beam("HP220x10", Justification.FLUSH_BOTTOM)
    e = np.asarray(resolve_justification(bm, Justification.FLUSH_BOTTOM), dtype=float)

    assert e[2] == pytest.approx(-0.22)


def test_a_circular_section_can_be_flush():
    """``curve_offset_local`` used to read ``h`` for it, which a round section does not have."""
    bm = ada.Beam("bm", (0, 0, 0), (4, 0, 0), "TUB200x10", up=(0, 0, 1), justification=Justification.FLUSH_TOP)
    data = bm.offset_helper.curve_offset_local()

    assert data.end1[2] == pytest.approx(-bm.section.r)


@pytest.mark.parametrize("section, expected", [("HP220x10", -0.1354302), ("TG300x500x20x32", 0.0963596)])
def test_na_on_an_angle_or_t_keeps_its_origin_on_the_node_line(section, expected):
    """The issue reads the non-zero NA value of an L or T as a bug (GeniE has 0). It is not changed.

    ``curve_offset_local`` is the GeniE-referenced offset (to the centroid) that puts the profile
    *origin* on the node line, which is what adapy's geometry, its Abaqus deck and its GeniE XML
    writer all do for a beam with no offset. What the Sesam path does with such a beam is a separate
    matter (it writes no GECCEN, like GeniE); see ``test_beam_ecc_from_justification``.
    """
    data = _beam(section, Justification.NA).offset_helper.curve_offset_local()

    assert data.end1[2] == pytest.approx(expected, abs=1e-6)

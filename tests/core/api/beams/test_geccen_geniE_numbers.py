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


@pytest.mark.parametrize("section", sorted(GENIE_FLUSH_Z))
@pytest.mark.parametrize("just", [Justification.NA, Justification.UNSET])
def test_a_beam_with_no_offset_has_its_centroid_on_the_node_line(section, just):
    """GeniE draws a beam with no curve offset (or ``no_flush``) on its centroid and writes no GECCEN; so does
    adapy now, for an angle and a T too. ``curve_offset_local`` (node to centroid) used to be the shift that put
    the profile *origin* on the node line (-0.1354302 for the HP, +0.0963596 for the T), so the geometry, the Abaqus
    deck and the GeniE XML writer placed such a beam one centroid shift off where GeniE has it (#419)."""
    bm = _beam(section, just)
    data = bm.offset_helper.curve_offset_local()

    assert data.end1 == data.end2 == (0.0, 0.0, 0.0)
    # its COG is on the node line
    assert np.allclose(np.asarray(bm.get_cog(), dtype=float), (2.0, 0.0, 0.0), atol=1e-9)
    # and the drawn profile's origin is placed so that its centroid is too
    from ada.api.beams.geom_beams import straight_beam_frame
    from ada.api.beams.justification import section_centroid_offset_local

    dz = float(section_centroid_offset_local(bm.section)[2])
    assert straight_beam_frame(bm).origin[2] == pytest.approx(-dz, abs=1e-9)


@pytest.mark.parametrize("section", sorted(GENIE_FLUSH_Z))
def test_top_of_steel_is_flush_top(section):
    """Top of steel is the top of the profile on the node line: E3D's CTOP, ``resolve_justification``, and GeniE's
    flush_top numbers. ``curve_offset_local`` had the opposite sign for it (+0.15 on an IPE300: the bottom of the
    steel on the node line), which is why the mesher used to report TOS rather than guess."""
    tos = _beam(section, Justification.TOS)
    data = tos.offset_helper.curve_offset_local()
    for end in (data.end1, data.end2):
        assert end[2] == pytest.approx(GENIE_FLUSH_Z[section][0], abs=1e-6)
    assert np.allclose(
        np.asarray(resolve_justification(tos, Justification.TOS), dtype=float),
        np.asarray(resolve_justification(tos, Justification.FLUSH_TOP), dtype=float),
    )

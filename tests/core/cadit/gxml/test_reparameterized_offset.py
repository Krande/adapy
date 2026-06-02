"""Regression tests for two interleaved gxml-reader bugs surfaced by
audit #5256:

* ``reparameterized_beam_curve_offset`` is a Genie wrapper around
  ``linear_varying_curve_offset`` that the reader used to ignore for
  axial-strip purposes — 280 stiffener T-beams ended up extended 1 m
  past their landing wall.
* Inverted T-sections (Genie's ``unsymmetrical_i_section`` with a
  degenerate top flange) get re-encoded into adapy's flange-up
  TPROFILE convention; the beam reader has to flip the local-z to
  keep the rendered shape pointing the right way.
"""

from __future__ import annotations

import textwrap
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from ada.cadit.gxml.read.read_beams import get_offsets


def _make_beam_el(curve_offset_xml: str) -> ET.Element:
    return ET.fromstring(
        textwrap.dedent(
            f"""
            <straight_beam name="bm">
                <curve_orientation>
                    <local_system>
                        <vector x="0" y="1" z="0" dir="x" />
                        <vector x="-1" y="0" z="0" dir="y" />
                        <vector x="0" y="0" z="1" dir="z" />
                    </local_system>
                </curve_orientation>
                <local_system>
                    <vector x="0" y="1" z="0" dir="x" />
                    <vector x="-1" y="0" z="0" dir="y" />
                    <vector x="0" y="0" z="1" dir="z" />
                </local_system>
                <segments>
                    <straight_segment index="1" section_ref="S">
                        <geometry><wire><guide>
                            <position x="0" y="0" z="0" end="1" />
                            <position x="0" y="-1.5" z="0" end="2" />
                        </guide></wire></geometry>
                    </straight_segment>
                </segments>
                {curve_offset_xml}
            </straight_beam>
            """
        )
    )


def test_reparameterized_offset_axial_stripped():
    # Mirrors audit #5256: linear_varying_curve_offset nested under
    # reparameterized_beam_curve_offset, with a -1 m axial (local-x)
    # component. The reader must drop the axial part so the beam
    # doesn't extend past its endpoint.
    bm_el = _make_beam_el(
        """
        <curve_offset>
            <reparameterized_beam_curve_offset>
                <curve_offset>
                    <linear_varying_curve_offset use_local_system="true">
                        <offset_end1 x="-1" y="0" z="-0.65" />
                        <offset_end2 x="0" y="0" z="-0.65" />
                    </linear_varying_curve_offset>
                </curve_offset>
            </reparameterized_beam_curve_offset>
        </curve_offset>
        """
    )
    o1, o2, use_local, container = get_offsets(bm_el)
    assert container == "reparameterized_beam_curve_offset"
    assert use_local is True
    # The reader returns the raw offset; the axial strip happens in
    # apply_offsets_and_alignments. Just confirm the parser sees the
    # right values + container.
    assert o1[0] == pytest.approx(-1.0)
    assert o2[0] == pytest.approx(0.0)


def test_curve_end_offset_container_recognized():
    # Existing path still works — keep_axial flags ride the same
    # mechanism.
    bm_el = _make_beam_el(
        """
        <curve_offset>
            <curve_end_offset keep_axial_eccentricity_at_end1="true"
                              keep_axial_eccentricity_at_end2="false">
                <curve_offset>
                    <constant_curve_offset use_local_system="true">
                        <constant_offset x="0.2" y="0" z="-0.1" />
                    </constant_curve_offset>
                </curve_offset>
            </curve_end_offset>
        </curve_offset>
        """
    )
    _, _, use_local, container = get_offsets(bm_el)
    assert container == "curve_end_offset"
    assert use_local is True


def test_bare_curve_offset_no_container():
    bm_el = _make_beam_el(
        """
        <curve_offset>
            <constant_curve_offset use_local_system="false">
                <constant_offset x="0" y="0" z="-0.3" />
            </constant_curve_offset>
        </curve_offset>
        """
    )
    _, _, use_local, container = get_offsets(bm_el)
    assert container is None
    assert use_local is False


def test_inverted_t_beam_section_lands_with_web_tip_on_wire():
    # Audit #5256 stiffener layout: wire at the wall plate, section
    # extends behind the wall with web tip flush on the plate. The
    # combination of the up-vector flip and the negated-Cgz add_local
    # has to land the section so web_tip.x == wire.x. Earlier we had
    # the FLANGE landing on the wire instead — section visually
    # off-by-h/2 in the wrong direction.
    from ada import Material, Part
    from ada.cadit.gxml.read.read_beams import el_to_beam
    from ada.cadit.gxml.read.read_sections import unsymm_isec

    sec_attrib = dict(h="1.0", bfbot="0.4", bftop="0.025", tw="0.025", tftop="0.0001", tfbot="0.03")
    sec = unsymm_isec("T1000x400x25x30", ET.Element("unsymmetrical_i_section", attrib=sec_attrib))

    parent = Part("p")
    parent.sections.add(sec)
    parent.materials.add(Material(name="S355"))

    # Wire runs along world -y at x=64.225 (the wall plate). Genie
    # original: zvector=(1,0,0). After our flange-down flip bm.up
    # ends up (-1,0,0). Offset is the audit-#5256 shape with the
    # reparameterized_beam_curve_offset wrapper + non-zero axial
    # component that must be stripped.
    bm_xml = textwrap.dedent(
        """\
        <straight_beam name="bm">
            <curve_orientation>
                <customizable_curve_orientation use_default_rule="false">
                    <orientation>
                        <local_system>
                            <xvector x="0" y="-1" z="0" />
                            <zvector x="1" y="0" z="0" />
                            <yvector x="0" y="0" z="-1" />
                        </local_system>
                    </orientation>
                </customizable_curve_orientation>
            </curve_orientation>
            <local_system>
                <vector x="0" y="-1" z="0" dir="x" />
                <vector x="0" y="0" z="-1" dir="y" />
                <vector x="1" y="0" z="0" dir="z" />
            </local_system>
            <segments>
                <straight_segment index="1" section_ref="T1000x400x25x30" material_ref="S355">
                    <geometry><wire><guide>
                        <position x="64.225" y="48.85" z="3.575" end="1" />
                        <position x="64.225" y="47.35" z="3.575" end="2" />
                    </guide></wire></geometry>
                </straight_segment>
            </segments>
            <curve_offset>
                <reparameterized_beam_curve_offset>
                    <curve_offset>
                        <linear_varying_curve_offset use_local_system="true">
                            <offset_end1 x="1" y="0" z="-0.6505172414" />
                            <offset_end2 x="0" y="0" z="-0.6505172414" />
                        </linear_varying_curve_offset>
                    </curve_offset>
                </reparameterized_beam_curve_offset>
            </curve_offset>
        </straight_beam>
        """
    )
    segs = el_to_beam(ET.fromstring(bm_xml), parent)
    assert len(segs) == 1
    bm = segs[0]
    assert tuple(bm.up) == (-1.0, 0.0, 0.0)

    # Where the section_center ends up after offset_helper +
    # straight_beam_to_geom's TPROFILE visual correction: net shift
    # along bm.up equal to ((-e1) · up). The visual correction
    # cancels the add_local term, so e1 alone drives the geometry's
    # final position.
    wire = np.array(bm.n1.p, dtype=float)
    up = np.array(bm.up, dtype=float)
    e1 = np.array(bm.e1, dtype=float)
    shift = float(np.dot(-e1, up))
    section_center = wire + shift * up
    web_tip = section_center + (-bm.section.h / 2.0) * up
    # Web tip lands on the wire's x (= the wall plate at 64.225).
    assert abs(web_tip[0] - 64.225) < 1e-6, web_tip


def test_inverted_t_beam_flips_up_vector():
    # Construct the beam path directly: build a Part with the
    # inverted-T section + material, invoke el_to_beam on the XML
    # snippet, and confirm the resulting beam carries a flipped
    # local-z. Going via ``from_genie_xml`` would also need a SAT
    # sidecar — overkill for a unit test of the reader contract.
    from ada import Material, Part
    from ada.cadit.gxml.read.read_beams import el_to_beam
    from ada.cadit.gxml.read.read_sections import unsymm_isec

    sec_attrib = dict(h="1.0", bfbot="0.4", bftop="0.025", tw="0.025", tftop="0.0001", tfbot="0.03")
    sec_el = ET.Element("unsymmetrical_i_section", attrib=sec_attrib)
    sec = unsymm_isec("T_FLIPPED", sec_el)
    assert sec.metadata.get("gxml_flange_down") is True

    parent = Part("p")
    parent.sections.add(sec)
    parent.materials.add(Material(name="S355"))

    bm_xml = textwrap.dedent(
        """\
        <straight_beam name="bm">
            <curve_orientation>
                <customizable_curve_orientation use_default_rule="false">
                    <orientation>
                        <local_system>
                            <xvector x="0" y="1" z="0" />
                            <zvector x="0" y="0" z="1" />
                            <yvector x="-1" y="0" z="0" />
                        </local_system>
                    </orientation>
                </customizable_curve_orientation>
            </curve_orientation>
            <local_system>
                <vector x="0" y="1" z="0" dir="x" />
                <vector x="-1" y="0" z="0" dir="y" />
                <vector x="0" y="0" z="1" dir="z" />
            </local_system>
            <segments>
                <straight_segment index="1" section_ref="T_FLIPPED" material_ref="S355">
                    <geometry><wire><guide>
                        <position x="0" y="0" z="0" end="1" />
                        <position x="0" y="-1.5" z="0" end="2" />
                    </guide></wire></geometry>
                </straight_segment>
            </segments>
        </straight_beam>
        """
    )
    bm_el = ET.fromstring(bm_xml)
    segs = el_to_beam(bm_el, parent)
    assert len(segs) == 1
    bm = segs[0]
    # Up vector flipped so the flange-up TPROFILE renders flange-down.
    assert tuple(bm.up) == (0.0, 0.0, -1.0)

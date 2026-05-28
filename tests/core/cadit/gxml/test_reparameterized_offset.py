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

import pytest

import ada
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


def test_inverted_t_beam_flips_up_vector():
    # Construct the beam path directly: build a Part with the
    # inverted-T section + material, invoke el_to_beam on the XML
    # snippet, and confirm the resulting beam carries a flipped
    # local-z. Going via ``from_genie_xml`` would also need a SAT
    # sidecar — overkill for a unit test of the reader contract.
    from ada import Material, Part
    from ada.cadit.gxml.read.read_beams import el_to_beam
    from ada.cadit.gxml.read.read_sections import unsymm_isec

    sec_attrib = dict(h="1.0", bfbot="0.4", bftop="0.025",
                      tw="0.025", tftop="0.0001", tfbot="0.03")
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

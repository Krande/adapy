"""Every section shape the Genie XML writer accepts must read back with all its dimensions.

``HP200x10`` did not: written as ``<l_section h b tw tf>`` and read back with ``w_top`` and
``t_ftop`` unset, because :func:`~ada.cadit.gxml.read.read_sections.angular` was the only
reader in that module that did not mirror the single written width/thickness onto both
flanges -- ``isec``, ``box_sec``, ``channel_section`` and ``bar_section`` all do.

The loss is invisible in geometry, which is why it survived: ``calc_angular`` and
``sections.profiles.angular`` read only ``h``, ``w_btn``, ``t_w`` and ``t_fbtn`` for an
ANGULAR section, so every ``GeneralProperties`` value round-tripped exactly while
``unique_props()`` -- the only section comparison that means anything across a file
boundary, since ``Section.__eq__`` is guid identity -- came back different.

So this sweeps *every* example in ``BaseTypes.get_valid_example_map_lists()`` rather than
just the one that was reported, and compares both halves: the dimensions and the properties
computed from them. Measured before the fix, HP was the only shape that lost anything.
"""

from __future__ import annotations

import pytest

import ada
from ada.cadit.gxml.write.write_sections import get_section_props
from ada.sections.categories import BaseTypes
from ada.sections.concept import GeneralProperties

# Every dimension ``Section.unique_props()`` compares, minus the two poly curves (which only
# a GENERAL/POLY section carries).
DIMENSIONS = ("h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt")

# ``BaseTypes.CIRCULAR`` has no branch in ``get_section_props`` -- the writer logs
# "not yet supported" and emits no ``<section>`` at all, so the beam's section_ref
# dangles and the read fails outright with ``ValueError: The section id "CIRC100" is
# not found``. That is a missing feature, not a silent dimension loss; it is pinned by
# ``test_circular_is_refused_by_name`` below so it stays named rather than skipped.
UNSUPPORTED = frozenset({BaseTypes.CIRCULAR})

SUPPORTED_EXAMPLES = sorted(
    (bt.value, name)
    for bt, names in BaseTypes.get_valid_example_map_lists().items()
    if bt not in UNSUPPORTED
    for name in names
)


def _round_trip(section_str: str, tmp_path) -> tuple[ada.Section, ada.Section]:
    """Write a one-beam model to Genie XML, read it back, return (source, read) section."""
    p = ada.Part("P") / ada.Beam("bm", (0, 0, 0), (2, 0, 0), section_str)
    a = ada.Assembly("rt") / p
    src = p.sections[0]

    xml_file = tmp_path / f"{section_str}.xml"
    a.to_genie_xml(xml_file)
    b = ada.from_genie_xml(xml_file)

    read = {sec.name: sec for part in b.get_all_parts_in_assembly(True) for sec in part.sections}
    assert src.name in read, f"{section_str}: section {src.name!r} is gone after the read ({sorted(read)})"
    return src, read[src.name]


def _property_values(gp: GeneralProperties) -> dict[str, float | None]:
    """Every computed cross-section property, by name. ``parent`` is a back-reference."""
    return {f: getattr(gp, f) for f in sorted(gp.__dataclass_fields__) if f != "parent"}


@pytest.mark.parametrize("type_value,section_str", SUPPORTED_EXAMPLES, ids=[n for _, n in SUPPORTED_EXAMPLES])
def test_every_supported_profile_keeps_all_its_dimensions(type_value, section_str, tmp_path):
    """Type and all eight dimensions, for every shape the writer has a branch for."""
    src, read = _round_trip(section_str, tmp_path)

    assert read.type == src.type, f"{section_str}: {src.type} read back as {read.type}"
    lost = {d: (getattr(src, d), getattr(read, d)) for d in DIMENSIONS if getattr(read, d) != getattr(src, d)}
    assert lost == {}, f"{section_str}: dimensions changed on the round trip (src, read): {lost}"


@pytest.mark.parametrize("type_value,section_str", SUPPORTED_EXAMPLES, ids=[n for _, n in SUPPORTED_EXAMPLES])
def test_every_supported_profile_keeps_its_computed_properties(type_value, section_str, tmp_path):
    """``Iy`` and friends are derived from the dimensions, so they must agree too.

    They already did for HP even with ``w_top``/``t_ftop`` lost -- nothing in the ANGULAR
    property or profile code reads them -- so this is not a restatement of the test above
    but the check that a dimension fix does not move any number that was already right.
    """
    src, read = _round_trip(section_str, tmp_path)

    a, b = _property_values(src.properties), _property_values(read.properties)
    assert sorted(a) == sorted(b)
    for name in sorted(a):
        av, bv = a[name], b[name]
        if av is None or bv is None:
            assert av is None and bv is None, f"{section_str}.{name}: {av} -> {bv}"
            continue
        assert bv == pytest.approx(av, rel=1e-9, abs=1e-15), f"{section_str}.{name}: {av} -> {bv}"


@pytest.mark.parametrize("type_value,section_str", SUPPORTED_EXAMPLES, ids=[n for _, n in SUPPORTED_EXAMPLES])
def test_every_supported_profile_round_trips_unique_props(type_value, section_str, tmp_path):
    """The whole-section comparison, since ``Section.__eq__`` is guid identity and so says
    nothing across a file boundary. This is the assertion a caller actually relies on."""
    src, read = _round_trip(section_str, tmp_path)
    assert read.unique_props() == src.unique_props(), section_str


def test_a_read_hp_beam_can_be_measured(tmp_path):
    """The loss was not only cosmetic: ``w_top=None`` is a value other code does arithmetic on.

    ``BoxSides._get_dim`` (``ada/api/bounding_box.py:220``) takes
    ``max(section.w_btn, section.w_top)`` for a beam, so every HP read back from a Genie XML
    raised ``TypeError: '>' not supported between instances of 'NoneType' and 'float'``.
    Measured on the pre-fix reader output; ``(2.0, 0.038, 0.2)`` after.
    """
    a = ada.Assembly("rt") / (ada.Part("P") / ada.Beam("bm", (0, 0, 0), (2, 0, 0), "HP200x10"))
    xml_file = tmp_path / "hp.xml"
    a.to_genie_xml(xml_file)

    (bm,) = ada.from_genie_xml(xml_file).get_all_physical_objects(by_type=ada.Beam)
    length, width, height = bm.bbox().sides._get_dim()[:3]
    assert (length, width, height) == pytest.approx((2.0, 0.038, 0.2))


def test_an_equal_leg_angle_gains_the_legs_its_string_parser_omits(tmp_path):
    """``L``-flavoured ANGULAR sections are the one place the mirror changes a value.

    ``string_to_section.angular_section`` builds ``L150x10`` with ``w_top``/``t_ftop`` left
    unset while ``profile_db_collect`` fills them for every HP, so adapy disagrees with
    itself about what an ANGULAR section carries. The reader now always fills them, which
    for an equal-leg angle means the legs it already has (``w_top == w_btn == 0.15``,
    ``t_ftop == t_fbtn == 0.01``) -- the same numbers, not invented ones, and the values that
    keep ``BoxSides._get_dim`` working (see above).

    Pinned rather than hidden: the remaining asymmetry is in the string parser, not in this
    reader, and closing it would change ``unique_props()`` for L sections everywhere.
    Geometry is unaffected either way -- the properties below are identical.
    """
    src, read = _round_trip("L150x10", tmp_path)

    assert (src.w_top, src.t_ftop) == (None, None), "the L string parser now fills these; drop this test"
    assert (read.w_top, read.w_btn) == (0.15, 0.15)
    assert (read.t_ftop, read.t_fbtn) == (0.01, 0.01)
    assert _property_values(read.properties) == pytest.approx(_property_values(src.properties))


def test_the_unsupported_set_is_exactly_what_the_writer_refuses():
    """Keeps ``UNSUPPORTED`` honest in both directions.

    ``get_section_props`` returning ``None`` is the writer's own refusal signal, so a shape
    gaining support (or silently losing it) fails here and forces the list above to be
    updated, instead of quietly dropping out of the sweep.
    """
    refused = set()
    for bt, names in BaseTypes.get_valid_example_map_lists().items():
        for name in names:
            sec = ada.Section.from_str(name)
            sec = sec[0] if isinstance(sec, list) else sec
            if get_section_props(sec) is None:
                refused.add(bt)
    assert refused == set(UNSUPPORTED), refused


def test_circular_is_refused_by_name(tmp_path):
    """A CIRCULAR section is dropped by the writer, and the read then cannot resolve the
    beam's section reference. Measured, so the gap is named rather than invisible.

    Note what the drop leaves behind: the ``<section>`` element is skipped but the beam's
    ``section_ref="CIRC100"`` is still written, so the file is internally inconsistent and
    only the read reports it -- the export itself emits one ERROR line and succeeds.
    """
    import xml.etree.ElementTree as ET

    a = ada.Assembly("rt") / (ada.Part("P") / ada.Beam("bm", (0, 0, 0), (2, 0, 0), "CIRC100"))
    xml_file = tmp_path / "circ.xml"
    a.to_genie_xml(xml_file)

    written = [el.attrib["name"] for el in ET.parse(xml_file).getroot().findall(".//section")]
    assert written == [], f"the writer now emits CIRCULAR; update UNSUPPORTED ({written})"
    with pytest.raises(ValueError, match="CIRC100"):
        ada.from_genie_xml(xml_file)


def test_a_general_section_keeps_every_explicit_property(tmp_path):
    """A GENERAL section has no dimensions at all -- it *is* its properties, written out as
    ``<general_section>`` with ``general_properties_method="explicit"``. The writer accepts
    it, so it belongs in this sweep even though it has no string form to parametrize over.
    """
    gp = GeneralProperties(
        Ax=0.012,
        Ix=3.4e-05,
        Iy=1.2e-04,
        Iz=5.6e-05,
        Iyz=7.8e-07,
        Wxmin=9.1e-04,
        Wymin=8.2e-04,
        Wzmin=7.3e-04,
        Shary=0.0031,
        Sharz=0.0042,
        Shceny=0.011,
        Shcenz=0.022,
        Sy=1.5e-04,
        Sz=2.5e-04,
        Sfy=1.0,
        Sfz=1.0,
    )
    src = ada.Section("GEN1", sec_type=ada.Section.TYPES.GENERAL, genprops=gp)
    a = ada.Assembly("rt") / (ada.Part("P") / ada.Beam("bm", (0, 0, 0), (2, 0, 0), sec=src))

    xml_file = tmp_path / "general.xml"
    a.to_genie_xml(xml_file)
    b = ada.from_genie_xml(xml_file)

    read = {sec.name: sec for part in b.get_all_parts_in_assembly(True) for sec in part.sections}["GEN1"]
    assert read.type == ada.Section.TYPES.GENERAL
    # Only the fields ``<general_section>`` carries; Cy/Cz/Cgy/Cgz have no attribute there.
    for name in sorted(_property_values(gp)):
        av = getattr(gp, name)
        if av is None:
            continue
        assert getattr(read.properties, name) == pytest.approx(av), name

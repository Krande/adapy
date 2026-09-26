"""``*Beam General Section`` properties: what may be substituted, and what must not be touched.

`eval_general_properties` fills in section properties a model does not carry. It used to treat
``Iyz == 0`` as missing data, but zero is the *correct* product of inertia for every section
symmetric about an axis -- box, tubular, I, circular, flatbar, channel; only an angle has a non-zero
one. The substitute it chose, ``(Iy + Iz) / 2``, is the largest value a product of inertia may
legally take, so it then failed the positive-definiteness test and ``Iy`` was inflated too.

Measured before the fix: a UNP200 channel went out with ``Iy`` 4.78x its true value, a UNP300 5.60x,
each with a fabricated ``I12``. Both reached the deck behind a log line. And because
``Section.properties`` caches, the function wrote those numbers into the model, so a Sesam or IFC
export later in the same process inherited them.

These tests pin both halves: computed zeros survive, and the model is never written to.
"""

from __future__ import annotations

import copy

import pytest

import ada
from ada.fem.formats.abaqus.write.write_sections import eval_general_properties
from ada.sections import GeneralProperties


@pytest.fixture
def channel() -> ada.Section:
    """A channel: the profile the defect was found on, and a computed ``Iyz`` of exactly zero.

    A channel no longer *reaches* the GENERAL path -- the writer traces one as ``section=ARBITRARY``
    now -- but these tests are about ``eval_general_properties`` itself, and what they need is a
    section whose product of inertia was computed and came out zero. That is still every channel, and
    it is also every box, tubular, I, circular and flatbar, so the fixture stays the profile the
    measurement was made on.
    """
    return ada.Section("UNP200", from_str="UNP200x10")


def test_a_computed_zero_product_of_inertia_is_kept(channel):
    """``Iyz == 0`` is the answer for a channel, not a gap in the data."""
    assert channel.properties.Iyz == 0.0, "precondition: a channel's Iyz is computed as exactly zero"

    gp = eval_general_properties(channel)

    assert gp.Iyz == 0.0


def test_the_major_axis_inertia_is_not_inflated(channel):
    """The consequence of fabricating Iyz was a 4.78x error in Iy on this very section."""
    before = copy.deepcopy(channel.properties)

    gp = eval_general_properties(channel)

    assert gp.Iy == pytest.approx(before.Iy)
    assert gp.Iz == pytest.approx(before.Iz)
    assert gp.Ix == pytest.approx(before.Ix)


def test_the_model_is_not_written_to(channel):
    """``Section.properties`` caches, so mutating it changes the model for every later export."""
    before = copy.deepcopy(channel.properties)

    gp = eval_general_properties(channel)

    assert channel.properties.Iy == pytest.approx(before.Iy)
    assert channel.properties.Iyz == pytest.approx(before.Iyz)
    assert gp is not channel.properties, "a copy must be returned, not the cached object"


def test_two_exports_in_one_process_get_the_same_answer(channel):
    """The contamination this prevents: export Abaqus, then export anything else."""
    first = eval_general_properties(channel)
    second = eval_general_properties(channel)

    assert first.Iy == pytest.approx(second.Iy)
    assert first.Iyz == pytest.approx(second.Iyz)


def test_an_angle_keeps_its_genuine_product_of_inertia():
    """An angle is the one profile with a real non-zero Iyz; it must pass through untouched."""
    angle = ada.Section("HP200", from_str="HP200x10")
    before = copy.deepcopy(angle.properties)
    assert before.Iyz != 0.0, "precondition: an angle has a non-zero Iyz"

    gp = eval_general_properties(angle)

    assert gp.Iyz == pytest.approx(before.Iyz)
    assert gp.Iy == pytest.approx(before.Iy)


def test_a_missing_product_of_inertia_becomes_zero_not_its_largest_legal_value():
    """When Iyz really is unknown, the neutral substitute is 0 -- symmetric. ``(Iy + Iz) / 2`` is
    the extreme of the legal range and guarantees tripping the positive-definiteness test."""
    sec = ada.Section("GEN", from_str="IPE200")
    sec.properties.Iyz = None

    gp = eval_general_properties(sec)

    assert gp.Iyz == 0.0


def test_properties_describing_no_real_section_raise_instead_of_being_adjusted():
    """Silently increasing Iy until the inequality holds is how a section becomes quietly stiffer."""
    sec = ada.Section("GEN", from_str="IPE200")
    sec._genprops = GeneralProperties(parent=sec, Ax=1.0, Ix=1.0, Iy=1.0, Iz=1.0, Iyz=5.0)

    with pytest.raises(ValueError, match="no real cross-section"):
        eval_general_properties(sec)


@pytest.mark.parametrize("attr", ["Ix", "Iy", "Iz"])
def test_a_missing_or_non_positive_second_moment_is_still_substituted(attr):
    """These cannot legitimately be zero for a real section, so zero does mean "no data" here --
    which is exactly why Iyz needs different treatment rather than the same rule."""
    sec = ada.Section("GEN", from_str="IPE200")
    setattr(sec.properties, attr, None)

    gp = eval_general_properties(sec)

    assert getattr(gp, attr) > 0.0


def test_a_declared_general_section_in_a_written_deck_carries_its_true_inertia(tmp_path):
    """End to end: the number that reaches the *Beam General Section* data line.

    This used to be written with a channel, because a channel was the profile that *reached* the
    GENERAL path. It no longer is: the Abaqus writer gained ``section=ARBITRARY``, which traces a
    channel's three walls exactly, so no channel goes out as a generalized section any more. The
    defect this guards is untouched by that -- it lives in ``eval_general_properties``, which every
    ``*Beam General Section`` still goes through -- so the test moves to the case that reaches it
    today: a section **declared** GENERAL, carrying properties symmetric about an axis.

    Those properties are a real channel's, so the numbers below are the same ones the original
    measurement was made on. Measured on 0.90.0 with the fix reverted, this exact deck:

        Iy   1.9270167e-05  ->  9.2119969e-05   (x 4.78)
        Iyz            0.0  ->  1.0488131e-05   (fabricated from a computed zero)

    Both behind a log line, and written into the cached ``GeneralProperties`` as well, so a later
    export in the same process inherited them.
    """
    channel_props = ada.Section("UNP200", from_str="UNP200x10").properties
    sec = ada.Section(
        "GENCHAN",
        sec_type="GENERAL",
        genprops=GeneralProperties(
            Ax=channel_props.Ax,
            Ix=channel_props.Ix,
            Iy=channel_props.Iy,
            Iz=channel_props.Iz,
            Iyz=0.0,
        ),
    )
    true_iy = sec.properties.Iy
    assert sec.properties.Iyz == 0.0, "precondition: the declared product of inertia is a computed zero"

    bm = ada.Beam("BM", (0, 0, 0), (2, 0, 0), sec=sec)
    p = ada.Part("P") / bm
    a = ada.Assembly("A") / p

    p.fem = p.to_fem_obj(mesh_size=100.0, experimental_bm_splitting=False)
    a.to_fem("gen", fem_format="abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    deck = (tmp_path / "gen" / "gen.inp").read_text()
    assert "*Beam General Section" in deck, "precondition: a declared GENERAL section goes out as one"

    lines = deck.splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("*Beam General Section"))
    numbers = [float(v) for v in lines[idx + 1].split(",") if v.strip()]
    assert any(
        v == pytest.approx(true_iy, rel=1e-9) for v in numbers
    ), f"the deck should carry the section's own Iy={true_iy:.6e}; its data line was {numbers}"
    assert numbers[2] == 0.0, f"and its computed zero Iyz, not a substitute; the data line was {numbers}"

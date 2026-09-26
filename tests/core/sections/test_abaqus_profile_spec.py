"""``Section`` -> Abaqus cross-section: the one mapping the INP writer and the CAE writer share.

Two things are being defended here, and they pull in opposite directions.

The first is that extracting the mapping out of ``write_sections.py`` changed nothing about what the
INP writer emits. A refactor of a dimension table is exactly where a silent order swap enters, and
no existing test would have caught one: the section still writes, the deck still analyses, the beam
is just the wrong shape. So the golden table below carries the writer's output for every section type
that worked before the extraction, byte for byte, and its sections are built with **every dimension
different** -- a real IPE300 has ``w_top == w_btn`` and ``t_ftop == t_fbtn``, so swapping that pair
is invisible on one. That is the trap this suite has been caught by before.

The second is that three things the mapping now says are true were measured against an Abaqus 2025
kernel and are not what reading the API would tell you:

* ``ArbitraryProfile`` is **thin-walled**: it is right for a channel, whose walls have a thickness
  each, and wrong for a filled outline (adapy's POLY), which it would misread by an order of
  magnitude;
* there is no ``section=T``, and Abaqus/CAE itself writes a T as an I with the bottom flange zeroed;
* there *is* a ``ChannelProfile`` in CAE and it is unusable -- Abaqus/Standard refuses the
  ``section=CHANNEL`` that CAE's own preprocessor writes for one, and CAE will not even compute the
  mass of a member carrying it. Both routes take the traced polyline instead: ``section=ARBITRARY``
  in the INP, ``ArbitraryProfile`` in CAE, from the same four points.

Each of those is pinned, because each is a plausible-sounding change away from a wrong model.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import FemSection, FemSet
from ada.fem.formats.abaqus.write.write_sections import (
    channel_arbitrary_lines,
    line_cross_sec_type_str,
    line_section_props,
)
from ada.sections.categories import BaseTypes
from ada.sections.profiles import (
    CAE_PROFILE_ARGUMENTS,
    CAE_PROFILE_CLASSES_THE_SOLVER_REJECTS,
    ProfileSpec,
    channel_cae_table,
    channel_midline_rows,
    eval_general_properties,
    profile_spec,
)


def asymmetric(name: str, sec_type: str, **dims) -> ada.Section:
    """A section with no two dimensions equal, so any swapped pair shows up in the output."""
    return ada.Section(name, sec_type=sec_type, **dims)


# Every dimension distinct and not a round multiple of another, so a swap cannot coincide.
I_ASYM = dict(h=0.81, w_btn=0.41, w_top=0.31, t_w=0.021, t_ftop=0.025, t_fbtn=0.029)
BOX_ASYM = dict(h=0.82, w_btn=0.42, w_top=0.32, t_w=0.022, t_ftop=0.026, t_fbtn=0.028)
T_ASYM = dict(h=0.83, w_btn=0.023, w_top=0.33, t_w=0.023, t_ftop=0.027, t_fbtn=0.027)
CHANNEL_ASYM = dict(h=0.24, w_btn=0.084, w_top=0.074, t_w=0.0084, t_ftop=0.0114, t_fbtn=0.0124)
L_ASYM = dict(h=0.19, w_btn=0.039, t_w=0.011, t_fbtn=0.0195)


def _fem_section(sec: ada.Section) -> FemSection:
    return FemSection(
        f"fs_{sec.name}",
        "line",
        FemSet(f"es_{sec.name}", [], "elset"),
        ada.Material("S355"),
        section=sec,
        local_y=(0.0, 0.0, 1.0),
    )


def written(sec: ada.Section) -> tuple[str, str]:
    """What the Abaqus INP writer puts on the keyword line and the data line, via its own functions."""
    fem_sec = _fem_section(sec)
    return line_cross_sec_type_str(fem_sec), line_section_props(fem_sec)


#: ``section=`` keyword and data line the writer produced for each type before the mapping was
#: extracted, captured by running the pre-refactor writer. ``n1`` is ``0.0, 0.0, 1.0`` throughout.
GOLDEN_INP = {
    "IPROFILE": (
        asymmetric("IASYM", "IG", **I_ASYM),
        "I",
        "0.405, 0.81, 0.41, 0.31, 0.029, 0.025, 0.021\n 0.0, 0.0, 1.0",
    ),
    "IPROFILE_IPE300": (
        ada.Section("IPE300", from_str="IPE300"),
        "I",
        "0.15, 0.3, 0.15, 0.15, 0.0107, 0.0107, 0.0071\n 0.0, 0.0, 1.0",
    ),
    "BOX": (
        asymmetric("BASYM", "BG", **BOX_ASYM),
        "BOX",
        "0.32, 0.82, 0.022, 0.026, 0.022, 0.028\n 0.0, 0.0, 1.0",
    ),
    "TUBULAR": (
        ada.Section("OD400", from_str="OD400x10"),
        "PIPE",
        "0.2, 0.01\n 0.0, 0.0, 1.0",
    ),
    "CIRCULAR": (
        ada.Section("CIRC100", from_str="CIRC100"),
        "CIRC",
        "0.1\n 0.0, 0.0, 1.0",
    ),
    "FLATBAR": (
        ada.Section("FB", from_str="FB100x10"),
        "RECT",
        "0.01, 0.1\n 0.0, 0.0, 1.0",
    ),
    "ANGULAR": (
        asymmetric("LASYM", "HP", **L_ASYM),
        "L",
        "0.039, 0.19, 0.0195, 0.011\n 0.0, 0.0, 1.0",
    ),
    "CHANNEL": (
        ada.Section("UNP200", from_str="UNP200x10"),
        "ARBITRARY",
        "3, 0.07075, -0.09425, 0.0, -0.09425, 0.0115\n"
        " 0.0, 0.09425, 0.0085\n"
        " 0.07075, 0.09425, 0.0115\n"
        " 0.0, 0.0, 1.0",
    ),
    # A declared GENERAL section, so that the one data line the mapping does not derive from
    # dimensions stays pinned too -- five properties, then n1, then E, G and alpha. Nothing else in
    # this table reaches `eval_general_properties` or the material line since the channel became an
    # ARBITRARY, and those numbers are the ones a symmetric section used to be corrupted in.
    "GENERAL": (
        ada.Section(
            "GenSec",
            sec_type="GENERAL",
            genprops=ada.sections.GeneralProperties(Ax=0.01, Ix=2.0e-6, Iy=1.0e-4, Iz=5.0e-5, Iyz=0.0),
        ),
        "GENERAL",
        "0.01, 0.0001, 0.0, 5e-05, 2e-06\n 0.0, 0.0, 1.0\n 210000000000.0, 80769230769.23077,1.2e-05",
    ),
}


@pytest.mark.parametrize("label", sorted(GOLDEN_INP))
def test_the_inp_output_is_unchanged_by_the_extraction(label):
    """Byte-for-byte, for every section type the writer handled before the mapping moved out.

    The asymmetric cases are the ones that bite: on ``IASYM`` all seven I-section dimensions differ,
    so ``b1``/``b2`` or ``t1``/``t2`` arriving the other way round fails here and only here.
    """
    sec, expected_kind, expected_props = GOLDEN_INP[label]

    kind, props = written(sec)

    assert kind == expected_kind
    assert props == expected_props


def test_the_golden_table_covers_every_type_that_used_to_work():
    """A parametrised golden that silently stopped covering a type would still be green.

    Eight types reached the writer before this change: I, BOX, PIPE, CIRC, RECT, L, GENERAL and the
    channel, which by then had its own keyword (ARBITRARY). Anything missing from the table is a type
    whose data line is no longer pinned at all.
    """
    covered = {GOLDEN_INP[label][0].type for label in GOLDEN_INP}

    assert covered == {
        BaseTypes.IPROFILE,
        BaseTypes.BOX,
        BaseTypes.TUBULAR,
        BaseTypes.CIRCULAR,
        BaseTypes.FLATBAR,
        BaseTypes.ANGULAR,
        BaseTypes.CHANNEL,
        BaseTypes.GENERAL,
    }
    assert len(GOLDEN_INP) == 9, "one entry per pinned case; a dropped entry is a dropped guarantee"


def test_a_t_profile_is_an_i_section_with_its_bottom_flange_zeroed():
    """New: a T used to raise ``Section type "BaseTypes.TPROFILE" is not added to Abaqus export yet``.

    There is no ``section=T`` keyword. Abaqus/CAE, asked to write a ``TProfile`` out, emits
    ``section=I`` with ``b1`` and ``t1`` zero and the flange in ``b2``/``t2`` -- so the flange must be
    in the *top* slots. Put it in ``b1``/``t1`` instead and the section is a T standing on its head:
    same area, same inertias, and every bending moment sign the wrong way round.
    """
    sec = asymmetric("TASYM", "TG", **T_ASYM)

    kind, props = written(sec)

    assert kind == "I"
    assert props == "0.415, 0.83, 0.0, 0.33, 0.0, 0.027, 0.023\n 0.0, 0.0, 1.0"
    dims = profile_spec(sec).inp_dims
    assert dims[2] == 0.0 and dims[4] == 0.0, "the zeroed flange is the bottom one, b1/t1"
    assert dims[3] == sec.w_top, "the real flange width belongs in b2"


def test_a_t_profile_becomes_a_cae_t_profile():
    """CAE does have a ``TProfile``, so the CAE route keeps the shape rather than faking an I."""
    spec = profile_spec(asymmetric("TASYM", "TG", **T_ASYM))

    assert spec.cae_class == "TProfile"
    assert spec.cae_kwargs == dict(b=0.33, h=0.83, l=0.415, tf=0.027, tw=0.023)


def test_a_channel_is_the_same_traced_polyline_on_both_routes():
    """A channel must not use CAE's ``ChannelProfile``, and the reason is a solver run, not taste.

    ``ChannelProfile`` exists, builds, and is a dead end. Measured on Abaqus 2025, on the INP that CAE
    itself exported from one, through ``abaqus datacheck``::

        ***ERROR: in keyword *BEAMSECTION, file "chan_job.inp", line 29: Illegal value
                  "CHANNEL" for parameter "section".
        ***ERROR: ELEMENT 1 INSTANCE CHANPART-1 IS MISSING A BEAM SECTION DEFINITION

    -- and ``part.getMassProperties()`` on the same member returns ``mass=None``, so the kernel will
    not integrate the profile either. Hence
    :func:`test_the_channel_profile_class_cannot_be_reached_by_accident`.

    What the two routes take instead is the *same* thing: ``section=ARBITRARY`` and
    ``ArbitraryProfile``, both fed the channel's three wall segments by centreline and thickness.
    Asked to export an INP for an ``ArbitraryProfile`` built from this table, Abaqus/CAE 2025 wrote
    the very data block the INP side writes, number for number -- which is the strongest form of "one
    mapping, not two" available, and the reason both rows are asserted here together.

    The midline arithmetic is asserted rather than only the class, because the Abaqus *reader*
    reconstructs ``h`` and ``w`` from these coordinates: ``x1`` is half a web thickness inside the
    flange tip, and each flange lies half a flange thickness inside its outer face. And the CAE table
    is asserted to be derived from the same rows, because the kernel does **not** validate a table's
    row width -- a five-float first row was accepted in silence and read back with every later row
    padded with zeros, i.e. a different profile.
    """
    sec = asymmetric("CHASYM", "UNP", **CHANNEL_ASYM)

    spec = profile_spec(sec)

    tip = sec.w_btn - sec.t_w / 2
    y_top = (sec.h - sec.t_ftop) / 2
    y_btn = -(sec.h - sec.t_fbtn) / 2

    assert spec.inp_kind == "ARBITRARY"
    assert spec.inp_dims == (3, tip, y_btn, 0.0, y_btn, sec.t_fbtn)
    assert spec.inp_extra_rows == ((0.0, y_top, sec.t_w), (tip, y_top, sec.t_ftop))

    assert spec.cae_class == "ArbitraryProfile"
    assert spec.cae_kwargs == {
        "table": (
            (tip, y_btn, 0.0),
            (0.0, y_btn, sec.t_fbtn),
            (0.0, y_top, sec.t_w),
            (tip, y_top, sec.t_ftop),
        )
    }, "the CAE table must be the INP rows relaid out, not a second derivation of the same shape"


def test_the_channels_two_spellings_are_one_polyline():
    """The same four points and three thicknesses, however they are packed onto lines.

    The keyword's first line carries the segment count and two points; CAE's table carries one
    ``(x, y, t)`` row per point with the first row's thickness unused. A drift between them would be a
    CAE model that is a different cross-section from the deck beside it, and nothing downstream
    compares the two.
    """
    sec = ada.Section("UNP200", from_str="UNP200x10")

    inp_rows = channel_midline_rows(sec)
    table = channel_cae_table(sec)

    count, x1, y1, x2, y2, t1 = inp_rows[0]
    assert count == 3 == len(table) - 1, "a channel is three segments and therefore four points"
    assert table[0] == (x1, y1, 0.0), "the first row is a starting point; its thickness is unused"
    assert table[1] == (x2, y2, t1)
    assert table[2:] == inp_rows[1:], "every later row is the keyword's own row, unchanged"
    assert profile_spec(sec).cae_kwargs["table"] == table


def test_the_name_the_reader_points_at_still_yields_the_block_it_reads():
    """``read_sections.channel_from_arbitrary``'s docstring names ``channel_arbitrary_lines`` as the
    thing it is the inverse of, and that function now forwards to the shared mapping. So it has no
    caller, and an uncalled function is exactly where a second spelling of the same numbers survives
    a change to the first. Pin the two together instead of deleting a name the reader cites.

    The reader's own acceptance conditions are asserted with it, because they are what the layout has
    to satisfy: six values on the first row starting with ``3``, then two rows of three, the web on
    ``x=0``, the flange tips equal and positive, and the top above the bottom.
    """
    sec = ada.Section("UNP200", from_str="UNP200x10")
    fem_sec = _fem_section(sec)

    block = channel_arbitrary_lines(sec)

    assert block == line_section_props(fem_sec).rsplit("\n", 1)[0]
    rows = [[float(v) for v in line.split(",") if v.strip()] for line in block.splitlines()]
    assert len(rows) == 3 and len(rows[0]) == 6 and rows[0][0] == 3
    assert len(rows[1]) == len(rows[2]) == 3
    _, x1, y1, x2, y2, _ = rows[0]
    (x3, y3, _), (x4, y4, _) = rows[1], rows[2]
    assert x2 == x3 == 0.0, "the web centreline is the local-2 axis"
    assert x1 == x4 > 0.0, "both flange tips, on the same side"
    assert y1 == y2 < 0.0 < y3 == y4, "bottom flange below, top flange above"


def test_the_channel_profile_class_cannot_be_reached_by_accident():
    """The guard that makes the measurement structural instead of a comment.

    ``mdb.models[..].ChannelProfile`` is right there, named after the shape, and builds without a
    murmur -- so "CAE has a ChannelProfile, use it" is a one-line change anybody would consider
    reasonable. It has to fail at construction, with the datacheck error in the message, rather than
    in a solver run days later.
    """
    assert "ChannelProfile" not in CAE_PROFILE_ARGUMENTS, "a rejected class needs no argument list"
    reason = "one class is refused outright and this is it; another needs the measurement to justify it"
    assert set(CAE_PROFILE_CLASSES_THE_SOLVER_REJECTS) == {"ChannelProfile"}, reason

    with pytest.raises(ValueError, match=r"Illegal value .CHANNEL."):
        ProfileSpec(
            base_type=BaseTypes.CHANNEL,
            inp_kind="ARBITRARY",
            inp_dims=(3, 0.07075, -0.09425, 0.0, -0.09425, 0.0115),
            cae_class="ChannelProfile",
            cae_kwargs=dict(l=0.1, h=0.2, b1=0.075, b2=0.075, t1=0.0115, t2=0.0115, t3=0.0085, o=0.0),
        )


def test_a_poly_is_refused_rather_than_given_invented_properties():
    """POLY has no properties at all, and the substitution turns that into a plausible section.

    ``calc_poly`` is a stub returning zeros. Fed through ``eval_general_properties`` those zeros come
    out as ``Ax=0, Iy=2.0, Iz=2.0, Ix=1.0``: a beam with no area and the stiffness of a 1.2 m solid
    square. It would write, it would analyse, and it would be nonsense -- so the mapping refuses.
    """
    from ada.api.curves import CurvePoly2d

    outer = CurvePoly2d(
        [(0.0, 0.0), (0.2, 0.0), (0.2, 0.3), (0.0, 0.3)],
        origin=(0, 0, 0),
        xdir=(1, 0, 0),
        normal=(0, 0, 1),
    )
    sec = ada.Section("PolySec", sec_type="poly", outer_poly=outer)

    # What is being prevented, spelled out: the fallback really does manufacture these numbers.
    substituted = eval_general_properties(sec)
    assert (substituted.Ax, substituted.Iy, substituted.Iz, substituted.Ix) == (0, 2.0, 2.0, 1.0)

    with pytest.raises(NotImplementedError, match="calc_poly"):
        profile_spec(sec)


def test_every_section_type_is_mapped():
    """No fallback branch: a new ``BaseTypes`` member must be added to the mapping deliberately.

    POLY is the one member with no usable mapping, and it raises for a reason it states.
    """
    examples = {
        BaseTypes.IPROFILE: ada.Section("IPE300", from_str="IPE300"),
        BaseTypes.BOX: ada.Section("BG", from_str="BG800x600x20x30"),
        BaseTypes.TUBULAR: ada.Section("OD400", from_str="OD400x10"),
        BaseTypes.CIRCULAR: ada.Section("CIRC100", from_str="CIRC100"),
        BaseTypes.FLATBAR: ada.Section("FB", from_str="FB100x10"),
        BaseTypes.ANGULAR: ada.Section("HP180", from_str="HP180x10"),
        BaseTypes.TPROFILE: ada.Section("TG", from_str="T650x300x25x40"),
        BaseTypes.CHANNEL: ada.Section("UNP200", from_str="UNP200x10"),
        BaseTypes.GENERAL: ada.Section(
            "GenSec",
            sec_type="GENERAL",
            genprops=ada.sections.GeneralProperties(Ax=0.01, Ix=2.0e-6, Iy=1.0e-4, Iz=5.0e-5, Iyz=0.0),
        ),
    }
    assert set(examples) | {BaseTypes.POLY} == set(BaseTypes), "a new section type needs a mapping"

    for base_type, sec in examples.items():
        spec = profile_spec(sec)
        assert spec.base_type is base_type
        assert spec.inp_kind in {"I", "BOX", "PIPE", "CIRC", "RECT", "L", "ARBITRARY", "GENERAL"}
        assert spec.cae_class in CAE_PROFILE_ARGUMENTS


#: The constructor arguments the Abaqus 2025 kernel reports for each class, in positional order --
#: a second copy of the probe result, so that editing the table in the source cannot quietly change
#: what the writers emit. Obtained by calling each class with distinct values and reading its members
#: back, plus the API docstrings where they exist.
#:
#: ``ChannelProfile`` is deliberately absent: its arguments were probed too, but the class cannot be
#: used at all (see `test_a_channel_is_a_generalized_section_on_both_routes`), and an argument list
#: sitting here is an invitation to use it. Its last argument ``o`` is the reason the list is not kept
#: "for reference": ``o`` has no documentation anywhere in the API, and what it does could only be
#: established by building a member with it and measuring -- which is exactly what nothing downstream
#: of a ``ChannelProfile`` will do. It was never proved, so it is not recorded.
PROBED_CAE_ARGUMENTS = {
    "ArbitraryProfile": ("table",),
    "BoxProfile": ("a", "b", "uniformThickness", "t1", "t2", "t3", "t4"),
    "CircularProfile": ("r",),
    "GeneralizedProfile": ("area", "i11", "i12", "i22", "j", "gammaO", "gammaW"),
    "IProfile": ("l", "h", "b1", "b2", "t1", "t2", "t3"),
    "LProfile": ("a", "b", "t1", "t2"),
    "PipeProfile": ("r", "t"),
    "RectangularProfile": ("a", "b"),
    "TProfile": ("b", "h", "l", "tf", "tw"),
}


def test_the_cae_argument_names_are_the_probed_ones():
    assert CAE_PROFILE_ARGUMENTS == PROBED_CAE_ARGUMENTS


def test_a_spec_with_the_wrong_cae_arguments_is_rejected_on_construction():
    """A misspelt keyword is a ``TypeError`` inside Abaqus, minutes into someone else's run."""
    with pytest.raises(ValueError, match="TProfile takes"):
        ProfileSpec(
            base_type=BaseTypes.TPROFILE,
            inp_kind="I",
            inp_dims=(0.1, 0.2),
            cae_class="TProfile",
            cae_kwargs=dict(b=0.3, h=0.8, l=0.4, t_f=0.02, t_w=0.01),
        )


def test_a_spec_with_the_arguments_out_of_order_is_rejected():
    """Right names, wrong order: the call still works and builds a different cross-section."""
    with pytest.raises(ValueError, match="IProfile takes"):
        ProfileSpec(
            base_type=BaseTypes.IPROFILE,
            inp_kind="I",
            inp_dims=(0.1,),
            cae_class="IProfile",
            cae_kwargs=dict(h=0.8, l=0.4, b1=0.3, b2=0.3, t1=0.02, t2=0.02, t3=0.01),
        )


@pytest.mark.parametrize(
    "sec",
    [
        asymmetric("IASYM", "IG", **I_ASYM),
        asymmetric("BASYM", "BG", **BOX_ASYM),
        ada.Section("OD400", from_str="OD400x10"),
        ada.Section("CIRC100", from_str="CIRC100"),
        ada.Section("FB", from_str="FB100x10"),
        asymmetric("LASYM", "HP", **L_ASYM),
    ],
    ids=["I", "BOX", "PIPE", "CIRC", "RECT", "L"],
)
def test_the_two_writers_carry_the_same_numbers(sec):
    """The anti-drift check, and the reason the mapping is one object rather than two tables.

    Probed: Abaqus writes a ``BoxProfile(a, b, OFF, t1..t4)`` out as ``section=BOX`` with exactly
    ``a, b, t1..t4``, and likewise for I, PIPE, CIRC and RECT and L. So for these types the INP data
    line and the CAE arguments must be the same numbers in the same order; if they ever differ, one
    of the two writers is producing a different beam from the other.

    T and CHANNEL are excluded on purpose: a T's CAE arguments are ``(b, h, l, tf, tw)`` against an
    I-section data line, and a channel's are one ``table`` against several keyword lines -- the same
    polyline, but not a positional match. Those two are tested above.
    """
    spec = profile_spec(sec)
    numeric = [v for k, v in spec.cae_kwargs.items() if k != "uniformThickness"]

    assert tuple(numeric) == spec.inp_dims


def test_a_constant_argument_is_emitted_unquoted():
    """``uniformThickness`` takes an ``abaqusConstants`` symbol; quoted, Abaqus rejects the call.

    Probed: ``BoxProfile(..., uniformThickness='OFF', ...)`` fails with "arg4; found String,
    expecting ON or OFF". And it cannot simply be left out -- with ``ON``, Abaqus reads ``t1`` only
    and reports ``t2 = t3 = t4 = 0``, i.e. a box with one wall thickness everywhere and no complaint.
    """
    spec = profile_spec(asymmetric("BASYM", "BG", **BOX_ASYM))

    source = spec.cae_kwargs_source()

    assert "uniformThickness=OFF" in source
    assert "'OFF'" not in source
    assert source.startswith("a=0.32, b=0.82, uniformThickness=OFF, t1=")

import pytest

from ada import Beam
from ada.api.fasteners import IntermittentSpec, Weld, WeldType, build_profile


@pytest.fixture
def two_beams():
    return (
        Beam("a", (0, 0, 0), (1, 0, 0), "IPE300"),
        Beam("b", (0, 0, 0), (0, 0, 1), "IPE300"),
    )


def test_weldtype_from_str_strict_round_trip():
    assert WeldType.from_str("FILLET") is WeldType.FILLET
    assert WeldType.from_str("fillet") is WeldType.FILLET
    assert WeldType.from_str("WELD_TYPE_FILLET") is WeldType.FILLET
    assert WeldType.from_str("weld_type_fillet") is WeldType.FILLET


def test_weldtype_from_str_unknown_raises():
    with pytest.raises(ValueError, match="Unknown weld type"):
        WeldType.from_str("CHEESEBURGER")


def test_weldtype_catalog_size():
    # 27 weld types ported from the upstream weld library.
    assert len(list(WeldType)) == 27


def test_weldprofileenum_alias_kept():
    from ada.api.fasteners import WeldProfileEnum

    assert WeldProfileEnum is WeldType


def test_build_profile_fillet_symmetric():
    pts = build_profile(WeldType.FILLET, throat=0.005)
    assert pts == [(0, 0), (-0.005, 0), (0, 0.005)]


def test_build_profile_fillet_asymmetric_legs():
    pts = build_profile(WeldType.FILLET, throat=0.005, leg1=0.008, leg2=0.006)
    assert pts == [(0, 0), (-0.008, 0), (0, 0.006)]


def test_build_profile_accepts_string_type():
    pts = build_profile("FILLET", throat=0.005)
    assert pts == [(0, 0), (-0.005, 0), (0, 0.005)]


def test_build_profile_unimplemented_raises():
    with pytest.raises(NotImplementedError, match="J_GROOVE_J_BUTT"):
        build_profile(WeldType.J_GROOVE_J_BUTT, throat=0.005)


def test_minimal_fillet_call_derives_profile(two_beams):
    bm_a, bm_b = two_beams
    weld = Weld(
        "minimal",
        p1=(0, 0, 0),
        p2=(0, 0, 1),
        weld_type=WeldType.FILLET,
        members=[bm_a, bm_b],
        xdir=(1, 0, 0),
        throat=0.005,
    )
    assert weld.type is WeldType.FILLET
    assert weld.throat == 0.005
    assert weld.sided == "one"
    assert weld.intermittent is None
    assert weld.geometry is not None


def test_explicit_profile_still_works(two_beams):
    """Backwards compatibility — passing profile= explicitly bypasses build_profile."""
    bm_a, bm_b = two_beams
    explicit = [(0, 0), (-0.01, 0), (0, 0.01)]
    weld = Weld(
        "explicit",
        p1=(0, 0, 0),
        p2=(0, 0, 1),
        weld_type=WeldType.FILLET,
        members=[bm_a, bm_b],
        profile=explicit,
        xdir=(1, 0, 0),
    )
    assert weld.type is WeldType.FILLET
    assert weld.throat is None


def test_missing_profile_and_throat_raises(two_beams):
    bm_a, bm_b = two_beams
    with pytest.raises(ValueError, match="profile is missing"):
        Weld(
            "bad",
            p1=(0, 0, 0),
            p2=(0, 0, 1),
            weld_type=WeldType.FILLET,
            members=[bm_a, bm_b],
            xdir=(1, 0, 0),
        )


def test_missing_xdir_raises(two_beams):
    bm_a, bm_b = two_beams
    with pytest.raises(ValueError, match="xdir"):
        Weld(
            "bad",
            p1=(0, 0, 0),
            p2=(0, 0, 1),
            weld_type=WeldType.FILLET,
            members=[bm_a, bm_b],
            throat=0.005,
        )


def test_missing_p1_p2_and_sweep_curve_raises(two_beams):
    bm_a, bm_b = two_beams
    with pytest.raises(ValueError, match="sweep_curve.*p1.*p2"):
        Weld(
            "bad",
            weld_type=WeldType.FILLET,
            members=[bm_a, bm_b],
            xdir=(1, 0, 0),
            throat=0.005,
        )


def test_sweep_curve_path(two_beams):
    """sweep_curve set → PrimSweep dispatch instead of PrimExtrude."""
    from ada import PrimSweep
    from ada.api.curves import CurveOpen3d

    bm_a, bm_b = two_beams
    curve = CurveOpen3d([(0, 0, 0), (0, 0, 0.5), (0, 0, 1)])
    weld = Weld(
        "swept",
        weld_type=WeldType.FILLET,
        members=[bm_a, bm_b],
        xdir=(1, 0, 0),
        throat=0.005,
        sweep_curve=curve,
    )
    assert weld.sweep_curve is curve
    assert weld.p1 is None
    assert weld.p2 is None
    assert isinstance(weld.geometry, PrimSweep)


def test_sided_and_intermittent_attrs(two_beams):
    bm_a, bm_b = two_beams
    intermittent = IntermittentSpec(pitch=0.1, length_on=0.05, length_off=0.05)
    weld = Weld(
        "intermittent",
        p1=(0, 0, 0),
        p2=(0, 0, 1),
        weld_type=WeldType.FILLET,
        members=[bm_a, bm_b],
        xdir=(1, 0, 0),
        throat=0.005,
        sided="two",
        intermittent=intermittent,
    )
    assert weld.sided == "two"
    assert weld.intermittent.pitch == 0.1
    assert weld.intermittent.length_on == 0.05

"""A beam section through adapy's own Sesam writer and reader, and the profile cards GeniE reads.

``GeneralProperties.modified`` decides GBEAMG's COMP (0 computed, 1 manually overridden; 89-7012
7.3.2). It compared the stored properties with the recalculated ones exactly, so every section read
back from a Sesam file -- whose fields carry 9 significant digits (E16.8) -- was "modified", and a
deck read and written again flipped every GBEAMG to COMP = 1 (measured on IPE300, BG400x300x12x20 and
TG300x200x10x15: Ax 5.18806e-3 stored, 5.1880599999999995e-3 recalculated).
"""

from __future__ import annotations

import functools
import pathlib
import re

import pytest

import ada

REF_DIR = pathlib.Path(__file__).resolve().parents[5] / "files/fem_files/sesam/section_props"
GENIE_FEMS = [REF_DIR / f"genie_v8_13_{n}_T1.FEM" for n in ("shear_areas", "shear_areas_edge", "torsion", "review437")]
#: GeniE V8.13-02 importing adapy_profile_cards_for_genie_import.FEM (see genie_v8_13_import.js)
GENIE_IMPORT = REF_DIR / "genie_v8_13_import_T1.FEM"


@functools.lru_cache(maxsize=None)
def _sections(path) -> dict[str, ada.Section]:
    a = ada.from_fem(path)
    return {s.name: s for p in a.get_all_parts_in_assembly(include_self=True) for s in p.sections}


def _write(a: ada.Assembly, tmp_path, tag: str) -> pathlib.Path:
    a.to_fem(tag, "sesam", scratch_dir=tmp_path, overwrite=True)
    return tmp_path / tag / f"{tag}T1.FEM"


def _model(section) -> ada.Assembly:
    p = ada.Part("p") / ada.Beam("bm", (0, 0, 0), (1, 0, 0), section)
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5, "line")
    return a


def _records(path: pathlib.Path, card: str) -> list[str]:
    return [m.group(0) for m in re.finditer(rf"^{card}.*?(?=\n[A-Z])", path.read_text(), re.S | re.M)]


@pytest.mark.parametrize(
    "sec_str", ["IPE300", "BG400x300x12x20", "L200x10", "UNP200", "FB200x50", "OD300x12", "CIRC100"]
)
def test_a_section_read_back_from_adapys_deck_is_not_modified(sec_str, tmp_path):
    """Write, read, write: the second deck's GBEAMG is the first's, COMP = 0 both times."""
    first = _write(_model(sec_str), tmp_path, "a1")
    back = ada.from_fem(first)
    (sec,) = [s for p in back.get_all_parts_in_assembly(include_self=True) for s in p.sections]
    assert not sec.properties.modified
    second = _write(back, tmp_path, "a2")
    assert _records(first, "GBEAMG") == _records(second, "GBEAMG")
    assert _records(second, "GBEAMG")[0].split()[2] == "0.00000000E+00"


def _genie_parametric():
    for path in GENIE_FEMS:
        for name, sec in _sections(path).items():
            # GeniE's library Ts carry a 0.001 mm bottom flange, which adapy's T leaves out (Shary 6.7e-5)
            if sec.type != sec.TYPES.GENERAL and not name.startswith("M1_"):
                yield pytest.param(path, name, id=f"{path.stem[13:]}-{name}")


@pytest.mark.parametrize("path, name", list(_genie_parametric()))
def test_a_section_genie_computed_is_not_modified(path, name):
    """GeniE computes from single-precision dimensions: its GBEAMG is within 1.6e-7 of adapy's
    recalculation for every parametric section in the reference files (largest: T03_ANGTW IX), and
    within 1.4e-17 m of zero where adapy's shear centre is exactly 0 (S10_BAR). Unmodified."""
    assert not _sections(path)[name].properties.modified


FIELDS = ("Ax", "Ix", "Iy", "Iz", "Wxmin", "Wymin", "Wzmin", "Shary", "Sharz", "Sy", "Sz")


@pytest.mark.parametrize("field", FIELDS + ("Shcenz", "Iyz"))
def test_a_change_of_ten_in_a_million_is_modified(field):
    """Ten times the tolerance, on any one field, is a modification (the shear centre and Iyz on
    their scales, sqrt(Ax) = 0.0855 m and sqrt(Iy Iz) = 3.5e-6 m^4 for this angle)."""
    sec = _sections(REF_DIR / "genie_v8_13_shear_areas_T1.FEM")["S07_ANG"]
    p = sec.properties
    calc = p.calc_parent_properties()
    scale = {"Shcenz": calc.Ax**0.5, "Iyz": (calc.Iy * calc.Iz) ** 0.5}.get(field, abs(getattr(calc, field)))
    original = getattr(p, field)
    try:
        setattr(p, field, getattr(calc, field) + 1e-5 * scale)
        assert p.modified
        setattr(p, field, getattr(calc, field) + 1e-7 * scale)
        assert not p.modified
    finally:
        setattr(p, field, original)


def test_a_missing_property_is_modified():
    """A property left out (None) where the calculation has one is not the calculated section."""
    p = _sections(REF_DIR / "genie_v8_13_shear_areas_T1.FEM")["S07_ANG"].properties
    original, p.Cz = p.Cz, None
    try:
        assert p.modified
    finally:
        p.Cz = original


# --- M1: a T, GeniE's own T encoding ------------------------------------------------------------

GENIE_T_FLANGE = 1e-6  # GeniE's library T (Libraries/tbar.xml): absent flange 0.001 mm thick, web + 0.001 mm wide


def test_a_t_round_trips_through_adapys_sesam_writer(tmp_path):
    """Write, read, write a TG300x200x10x15: it comes back a TPROFILE with its dimensions, unmodified,
    and the second deck's GIORH and GBEAMG are the first's. Before: an IPROFILE whose recalculation
    counted the stub as a flange (Shary 3.82768e-3 for the stored 1.91384e-3, Ix 4.24125e-7 for
    4.16000e-7), modified, and the second GBEAMG had COMP = 1."""
    first = _write(_model(ada.Section("T", from_str="TG300x200x10x15")), tmp_path, "t1")
    back = ada.from_fem(first)
    (sec,) = [s for p in back.get_all_parts_in_assembly(include_self=True) for s in p.sections]
    assert sec.type == sec.TYPES.TPROFILE
    assert (sec.h, sec.w_top, sec.t_w, sec.t_ftop) == pytest.approx((0.3, 0.2, 0.01, 0.015), rel=1e-12)
    assert sec.unique_props() == ada.Section("T", from_str="TG300x200x10x15").unique_props()  # slots too
    assert not sec.properties.modified
    assert sec.properties.Cgz == pytest.approx(sec.properties.calc_parent_properties().Cgz, rel=1e-12)
    second = _write(back, tmp_path, "t2")
    assert _records(first, "GIORH") == _records(second, "GIORH")
    assert _records(first, "GBEAMG") == _records(second, "GBEAMG")
    assert _records(second, "GBEAMG")[0].split()[2] == "0.00000000E+00"


def test_a_t_is_written_as_genies_own_t(tmp_path):
    """GIORH BB = TY + 0.001 mm, TB = 0.001 mm, as GeniE writes its library Ts. adapy wrote the
    web-wide stub (BB = TY, TB = TT), which GeniE reads as an I (next test)."""
    deck = _write(_model(ada.Section("T", from_str="TG300x200x10x15")), tmp_path, "t")
    _, hz, ty, bt, tt, bb, tb, sfy, sfz = (float(x) for x in _records(deck, "GIORH")[0].split()[1:])
    assert (hz, ty, bt, tt) == pytest.approx((0.3, 0.01, 0.2, 0.015), rel=1e-12)
    assert bb == pytest.approx(ty + GENIE_T_FLANGE, rel=1e-12) and tb == pytest.approx(GENIE_T_FLANGE, rel=1e-12)


def _genie_import(name: str):
    return _sections(GENIE_IMPORT)[name].properties


def test_genie_recomputes_genies_t_encoding_as_the_t():
    """GeniE recomputes GBEAMG from the profile card on import (it drops a GBEAMG IX x 10, under COMP
    0 or 1). From the card in its own T encoding (T_GENIE, TG300x200x10x16) it computes adapy's T:
    IX and WXMIN within 8.5e-7 (its web is 0.001 mm shorter), the rest within 1.3e-7, SHARY too once
    its 0.001 mm flange (delta / t_ftop = 6.25e-5) is divided out. From the stub adapy wrote before (T_STUB, TG300x200x10x15) it computes an I:
    SHARY 3.82768e-3, 2.000 x the T's 1.91384e-3, IX 4.24125e-7 for 4.16000e-7."""
    from ada.sections.properties import calculate_general_properties

    tee = calculate_general_properties(ada.Section("T", from_str="TG300x200x10x16"))
    genie = _genie_import("T_GENIE")
    for f in FIELDS + ("Shceny", "Shcenz"):
        r = getattr(genie, f) * (0.016 / (0.016 + GENIE_T_FLANGE) if f == "Shary" else 1.0)
        assert r == pytest.approx(getattr(tee, f), rel=1e-5, abs=1e-12), f
    tee = calculate_general_properties(ada.Section("T", from_str="TG300x200x10x15"))
    stub = _genie_import("T_STUB")
    assert stub.Shary == pytest.approx(2 * tee.Shary, rel=1e-6)
    assert stub.Ix == pytest.approx(4.24125e-7, rel=1e-6) and tee.Ix == pytest.approx(4.16e-7, rel=1e-12)
    assert _genie_import("I_IX10").Ix == pytest.approx(2.67018e-7, rel=1e-5)
    assert _genie_import("I_COMP1").Ix == pytest.approx(4.86626e-7, rel=1e-5)


def test_the_reader_tells_genies_t_from_genies_stub_i():
    """GeniE's library T (0.001 mm bottom flange) reads as a TPROFILE; an unsymmetrical I whose
    bottom flange is a stub as wide as the web but as thick as a flange (S03_TEE, 15 mm) stays the
    IPROFILE GeniE computes it as (Shary with both flanges)."""
    assert {n: s.type for n, s in _sections(GENIE_FEMS[3]).items() if n.startswith("M1_")} == {
        n: ada.Section.TYPES.TPROFILE for n in ("M1_T300", "M1_TG650", "M1_TEQ", "M1_E1T")
    }
    s03 = _sections(GENIE_FEMS[0])["S03_TEE"]
    assert s03.type == s03.TYPES.IPROFILE and not s03.properties.modified


@pytest.mark.parametrize(
    "bb, tb, expected",
    [(0.010001, 1e-6, "TPROFILE"), (0.01, 0.015, "IPROFILE"), (0.2, 1e-6, "IPROFILE"), (0.0105, 0.002, "IPROFILE")],
    ids=["genie-t", "stub", "wide-paper-flange", "2mm-flange"],
)
def test_a_giorh_is_a_t_when_its_bottom_flange_is_paper_thin_and_web_wide(bb, tb, expected):
    """At most 1 mm thick and at most 1 mm past the web (the Genie XML reader's thresholds)."""
    from types import SimpleNamespace

    from ada.fem.formats.sesam.read import cards
    from ada.fem.formats.sesam.read.read_sections import get_isection
    from ada.fem.formats.sesam.write.write_utils import write_ff

    text = write_ff("GIORH", [(1, 0.3, 0.01, 0.2), (0.015, bb, tb, 1.0), (1.0,)]) + "IEND\n"
    sec = get_isection(cards.GIORH.to_ff_re().search(text), {1: "S"}, SimpleNamespace(parent=None))
    assert sec.type == getattr(ada.Section.TYPES, expected)


# --- M2: GLSEC web orientation K ---------------------------------------------------------------


def _angle() -> ada.Section:
    return ada.Section("L200", sec_type="L", h=0.2, w_btn=0.1, w_top=0.1, t_w=0.01, t_fbtn=0.014, t_ftop=0.014)


def _glsec_k(path: pathlib.Path) -> float:
    (rec,) = _records(path, "GLSEC")
    return float(rec.split()[8])


def test_an_angle_is_written_with_its_web_on_the_negative_y_side(tmp_path):
    """GLSEC K = 0: web towards -y, flange towards +y (89-7012 7.3.19) -- adapy's outline, and the
    side its SHCENY (-1.93252e-2 for this angle) is on. GeniE writes K = 0 with the same SHCENY
    (S07_ANG). adapy wrote K = 1, the mirror."""
    deck = _write(_model(_angle()), tmp_path, "l")
    assert _glsec_k(deck) == 0.0
    (gb,) = _records(deck, "GBEAMG")
    assert float(gb.split()[13]) == pytest.approx(-1.93252e-2, rel=1e-5)


def test_genie_does_not_mirror_an_angle_written_with_k_1():
    """GeniE V8.13-02 imports GLSEC K = 1 (L_K1) exactly as K = 0 (L_K0): it writes both back with
    K = 0, the beams' local systems unchanged, SHCENY on the web side (-1.93252e-2, -2.01493e-2) as
    adapy calculates for the unmirrored angle. Sestra V11.3-01 gives a cantilever of L_K1's section
    under a tip load Fy = Fz = 1 kN bit-identical displacements with K = 0 and K = 1."""
    from ada.sections.properties import calculate_general_properties

    for name, tf in (("L_K1", 0.014), ("L_K0", 0.015)):
        sec = _sections(GENIE_IMPORT)[name]
        ref = calculate_general_properties(
            ada.Section("L", sec_type="L", h=0.2, w_btn=0.1, w_top=0.1, t_w=0.01, t_fbtn=tf, t_ftop=tf)
        )
        assert sec.properties.Shceny == pytest.approx(ref.Shceny, rel=1e-6) and ref.Shceny < 0
    assert [float(r.split()[8]) for r in _records(GENIE_IMPORT, "GLSEC")] == [0.0, 0.0]


def _with_k(deck: pathlib.Path, k: float) -> pathlib.Path:
    (rec,) = _records(deck, "GLSEC")
    fields = rec.split()
    fields[8] = f"{k:.8E}"
    lines = rec.splitlines()
    lines[1] = " " * 8 + "".join(f"{x:>16}" for x in fields[5:9])
    deck.write_text(deck.read_text().replace(rec, "\n".join(lines)))
    return deck


@pytest.mark.parametrize("k", [0.0, 1.0])
def test_an_angle_read_with_k_1_is_reported(k, tmp_path):
    """adapy has one angle outline, web towards -y. A GLSEC with K = 1 (flange towards -y) is read as
    that outline -- as GeniE reads it -- with its GBEAMG, and an "approximated" finding names it."""
    from ada.fem.formats import conversion_report

    deck = _with_k(_write(_model(_angle()), tmp_path, "l"), k)
    assert _glsec_k(deck) == k
    with conversion_report.collect() as rep:
        sec = list(_sections.__wrapped__(deck).values())[0]
    found = [f for f in rep.findings if f.keyword == "GLSEC"]
    assert sec.type == sec.TYPES.ANGULAR
    if k == 0.0:
        assert not found
    else:
        (f,) = found
        assert f.kind == "approximated" and f.subject == "L200" and "K = 1" in f.reason


# --- L1: a GLSEC that is not an angle ------------------------------------------------------------


def test_a_file_with_a_glsec_that_is_not_an_angle_reads(tmp_path):
    """GLSEC HZ 0.1, TY 0.01, BY 0.1, TZ 0.1: h <= tf, no web. adapy refuses to calculate it (Roark's
    formula divides by the web's free length), and the Sesam reader -- which calculates every angle
    to fill the centroid and shear-centre fields GBEAMG lacks -- lost the whole model to that
    ValueError. Now the model reads, the section keeps its GBEAMG with Cy/Cz/Cgy/Cgz None, and a
    "suspect" finding names it. Asking for its calculated properties still raises."""
    from ada.fem.formats import conversion_report
    from ada.fem.formats.sesam.write.write_utils import write_ff
    from ada.sections.properties import calculate_general_properties

    deck = _write(_model(_angle()), tmp_path, "l")
    (rec,) = _records(deck, "GLSEC")
    bad = write_ff("GLSEC", [(1, 0.1, 0.01, 0.1), (0.1, 1.0, 1.0, 0)]).rstrip("\n")
    deck.write_text(deck.read_text().replace(rec, bad))
    with conversion_report.collect() as rep:
        (sec,) = _sections.__wrapped__(deck).values()
    p = sec.properties
    assert sec.type == sec.TYPES.ANGULAR and sec.h == 0.1 and sec.t_fbtn == 0.1
    assert p.Ax == pytest.approx(3.26e-3, rel=1e-8)  # the GBEAMG written for the real angle
    assert (p.Cy, p.Cz, p.Cgy, p.Cgz) == (None, None, None, None)
    (f,) = [f for f in rep.findings if f.subject == "L200"]
    assert f.kind == "suspect" and "is not an angle" in f.reason
    with pytest.raises(ValueError, match='"L200" is not an angle'):
        calculate_general_properties(sec)


# --- L3: the shear factors SFY/SFZ on a profile card ------------------------------------------------


def _rewrite(deck: pathlib.Path, card: str, edit) -> pathlib.Path:
    from ada.fem.formats.sesam.write.write_utils import write_ff

    rec = _records(deck, card)[0]
    vals = edit([float(x) for x in rec.split()[1:]])
    new = "" if vals is None else write_ff(card, [tuple(vals[i : i + 4]) for i in range(0, len(vals), 4)])
    deck.write_text(deck.read_text().replace(rec + "\n", new))
    return deck


def _factors(sfy, sfz):
    def edit(v):  # GIORH: geono hz ty bt tt bb tb sfy sfz
        v[7], v[8] = sfy, sfz
        return v

    return edit


def test_a_card_factor_without_a_gbeamg_is_applied(tmp_path):
    """89-7012: SHARY = SHARY(calculated) x SFY. A GIORH SFY 0.5 / SFZ 0.8 with no GBEAMG beside it
    (a hand-made or third-party deck; GeniE always writes GBEAMG) was read with the factors dropped
    -- Sfy 1, Shary 2.08222e-3 for IPE300 -- and written back SFY 1.0. Now the section's properties
    are calculated with them: Shary 1.04111e-3, Sharz 1.50920e-3 (GeniE's L3_I GBEAMG), and the card
    says 0.5 / 0.8 again."""
    deck = _write(_model(ada.Section("I", from_str="IPE300")), tmp_path, "i")
    _rewrite(_rewrite(deck, "GIORH", _factors(0.5, 0.8)), "GBEAMG", lambda v: None)
    assert not _records(deck, "GBEAMG")
    (sec,) = _sections.__wrapped__(deck).values()
    p = sec.properties
    assert (p.Sfy, p.Sfz) == (0.5, 0.8)
    assert (p.Shary, p.Sharz) == pytest.approx((1.04111050e-3, 1.50919675e-3), rel=1e-7)
    again = _write(ada.from_fem(deck), tmp_path, "i2")
    assert [float(x) for x in _records(again, "GIORH")[0].split()[8:10]] == [0.5, 0.8]
    assert float(_records(again, "GBEAMG")[0].split()[11]) == pytest.approx(1.04111050e-3, rel=1e-7)


def test_a_zero_shear_factor_is_a_zero_shear_area():
    """89-7012 gives SFY no default: SHARY(MOD) = SHARY(PROG) x SFY, and a zero SHARY means shear
    deformation is not included. GeniE V8.13-02 imports GIORH SFY 0 / SFZ 0 (I_SF0) and writes SHARY
    = SHARZ = 0 beside SFY 0 -- 0 is not "not given". adapy reads it so: factors 0, unmodified."""
    p = _sections(GENIE_IMPORT)["I_SF0"].properties
    assert (p.Sfy, p.Sfz, p.Shary, p.Sharz) == (0.0, 0.0, 0.0, 0.0)
    assert not p.modified


def test_a_zero_shear_factor_beside_a_shear_area_is_suspect(tmp_path):
    """SFY 0 beside a GBEAMG SHARY that is not zero contradicts itself: Sestra takes GBEAMG's SHARY,
    GeniE recomputes 0. The GBEAMG and the factor are both kept as read, and a "suspect" finding
    names the section."""
    from ada.fem.formats import conversion_report

    deck = _write(_model(ada.Section("I", from_str="IPE300")), tmp_path, "i")
    _rewrite(deck, "GIORH", _factors(0.0, 1.0))
    with conversion_report.collect() as rep:
        (sec,) = _sections.__wrapped__(deck).values()
    p = sec.properties
    assert (p.Sfy, p.Sfz) == (0.0, 1.0) and p.Shary == pytest.approx(2.08222099e-3, rel=1e-8)
    (f,) = [f for f in rep.findings if f.keyword == "GIORH"]
    assert f.kind == "suspect" and f.subject == "IPE300" and "SFY" in f.reason

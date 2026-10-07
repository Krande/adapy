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
GENIE_FEMS = sorted(REF_DIR.glob("genie_v8_13_*_T1.FEM"))


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
            # GeniE's Ts carry a 0.001 mm bottom flange, which adapy's T leaves out (Shary 6.7e-5 apart)
            if sec.type != sec.TYPES.GENERAL and not name.startswith(("M1_", "S03_", "E1_", "T11_")):
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

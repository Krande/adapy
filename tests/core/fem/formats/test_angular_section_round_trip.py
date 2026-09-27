"""An ANGULAR section must read back from an Abaqus or Sesam deck as the section written.

adapy carries an angle's one flange in both flange slots -- ``profile_db_collect`` fills
``w_top``/``t_ftop`` for every HP, ``string_to_section`` for every L -- while ``section=L``
and ``GLSEC`` carry one width and one thickness. Both readers used to put them in the bottom
slots only, so an ``HP180x10`` read back with ``w_top=None, t_ftop=None`` from either deck,
and ``unique_props()`` -- the only section comparison that survives a file boundary, since
``Section.__eq__`` is guid identity -- disagreed. Nothing in the ANGULAR geometry reads the
top slots, so no property or deck line moves with them.
"""

from __future__ import annotations

import pytest

import ada

DECKS = {"abaqus": "m.inp", "sesam": "mT1.FEM"}
ANGLES = ("HP180x10", "L150x10")


def _round_trip(fmt: str, section_str: str, tmp_path) -> tuple[ada.Section, ada.Section]:
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), section_str)
    p = ada.Part("P") / bm
    p.fem = p.to_fem_obj(1.0, "line")
    (ada.Assembly("A") / p).to_fem("m", fmt, scratch_dir=tmp_path, overwrite=True)

    b = ada.from_fem(tmp_path / "m" / DECKS[fmt])
    (read,) = {
        fs.section.name: fs.section for part in b.get_all_parts_in_assembly(True) for fs in part.fem.sections.lines
    }.values()
    return bm.section, read


@pytest.mark.parametrize("section_str", ANGLES)
@pytest.mark.parametrize("fmt", sorted(DECKS))
def test_an_angle_reads_back_as_the_section_written(fmt, section_str, tmp_path):
    src, read = _round_trip(fmt, section_str, tmp_path)

    assert read.type == src.type
    assert (read.w_top, read.t_ftop) == (src.w_top, src.t_ftop), f"{fmt} {section_str}: top flange slots"
    assert read.unique_props() == src.unique_props(), f"{fmt} {section_str}"

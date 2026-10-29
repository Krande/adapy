"""``ada convert`` on a Sesam superelement assembly (.SIN): one file per superelement, one chosen, or all merged.

The assembly is the one ``sin_assembly.write_assembly_sin`` splices together: a top level of type 100 over
two copies of a small cantilever as types 10 and 11, the second placed with a rotation.
"""

from __future__ import annotations

import argparse
import pathlib
import re

import pytest

from ada.api.cli import (
    _cmd_convert,
    _input_name_number,
    _numbered_output,
    _resolve_superelement,
)
from ada_cli import CliUsageError

from . import sin_assembly
from .sin_assembly import SIN_PATH, write_assembly_sin

TRANSLATION_ONLY = ((1.0, 0.0, 0.0, 10.0), (0.0, 1.0, 0.0, 20.0), (0.0, 0.0, 1.0, 30.0), (0.0, 0.0, 0.0, 1.0))


def _args(input_file, output, **kw) -> argparse.Namespace:
    values = dict(
        input=str(input_file),
        output=str(output),
        from_format=None,
        to_format=None,
        split=False,
        limit=None,
        strict=False,
        superelement=None,
        super_element=None,
        merge=False,
    )
    values.update(kw)
    return argparse.Namespace(**values)


def _ident_seltyp(path: pathlib.Path) -> int:
    first = path.read_text().splitlines()[0]
    assert first.startswith("IDENT")
    return int(float(first.split()[2]))


def _count(path: pathlib.Path, card: str) -> int:
    return len(re.findall(rf"^{card}\s", path.read_text(), re.MULTILINE))


@pytest.fixture(scope="module")
def assembly(tmp_path_factory):
    return write_assembly_sin(tmp_path_factory.mktemp("conv") / "ASSEMBLY.SIN")


@pytest.fixture(scope="module")
def translated(tmp_path_factory):
    import unittest.mock

    with unittest.mock.patch.object(sin_assembly, "PLACEMENT_ROWS", TRANSLATION_ONLY):
        return write_assembly_sin(tmp_path_factory.mktemp("conv_t") / "ASSEMBLY.SIN")


# ── naming and the resolution order ────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [("out.FEM", "outT10.FEM"), ("outT1.FEM", "outT10.FEM"), ("T5.FEM", "T10.FEM"), ("a.b.fem", "a.bT10.fem")],
)
def test_numbered_output_names(name, expected):
    assert _numbered_output(pathlib.Path("somedir") / name, 10) == pathlib.Path("somedir") / expected


def test_superelement_resolution_order(caplog):
    out_plain, out_t = pathlib.Path("out.FEM"), pathlib.Path("outT7.FEM")
    assert _resolve_superelement(out_t, 7, 5, "R9.SIN") == 7  # explicit
    assert _resolve_superelement(out_t, None, 5, "R9.SIN") == 7  # the output's T-number beats the input
    assert _resolve_superelement(out_plain, None, 5, "R9.SIN") == 5  # what the input says beats its name
    assert _resolve_superelement(out_plain, None, None, "R9.SIN") == 9  # the input's name
    assert _resolve_superelement(out_plain, None, None, "deckT12.FEM") == 12
    assert _resolve_superelement(out_plain, None, None, "plain.FEM") == 1  # nothing says
    assert _resolve_superelement(out_plain, None) == 1


def test_superelement_explicit_still_contradicting_the_output_name():
    with pytest.raises(CliUsageError, match="contradicts"):
        _resolve_superelement(pathlib.Path("outT7.FEM"), 8, 5, "R9.SIN")


def test_input_name_numbers():
    assert _input_name_number("R7.SIN") == 7
    assert _input_name_number("somewhere/prefixT3.FEM") == 3
    assert _input_name_number("model.FEM") is None


# ── the reader keeps the IDENT ───────────────────────────────────────────────────


def test_fem_reader_keeps_the_ident_seltyp(tmp_path):
    from ada.fem.formats.sesam.read.reader import read_fem

    deck = tmp_path / "deck.FEM"
    deck.write_text(
        "IDENT       1.00000000E+00  4.20000000E+01  3.00000000E+00  0.00000000E+00\n"
        "IEND        0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
    )
    part = read_fem(deck).get_all_parts_in_assembly(include_self=False)[0]
    assert part.fem.metadata["sesam_seltyp"] == 42


# ── default: one file per first-level superelement ──────────────────────────────


def test_assembly_is_written_as_one_numbered_file_per_superelement(assembly, tmp_path):
    out = tmp_path / "result.FEM"
    assert _cmd_convert(_args(assembly, out)) == 0

    assert sorted(p.name for p in tmp_path.glob("*.FEM")) == ["resultT10.FEM", "resultT11.FEM"]
    assert not out.exists()
    for number in (10, 11):
        deck = tmp_path / f"resultT{number}.FEM"
        assert _ident_seltyp(deck) == number
        assert _count(deck, "GCOORD") == 403 and _count(deck, "GELMNT1") == 360


def test_numbered_output_name_is_not_doubled(assembly, tmp_path):
    _cmd_convert(_args(assembly, tmp_path / "resultT1.FEM"))
    assert sorted(p.name for p in tmp_path.glob("*.FEM")) == ["resultT10.FEM", "resultT11.FEM"]


def test_super_element_flag_converts_just_that_one_as_out(assembly, tmp_path):
    out = tmp_path / "chosen.FEM"
    assert _cmd_convert(_args(assembly, out, super_element=["11"])) == 0
    assert [p.name for p in tmp_path.glob("*.FEM")] == ["chosen.FEM"]
    assert _ident_seltyp(out) == 11


def test_several_super_element_flags_write_numbered_files(assembly, tmp_path):
    _cmd_convert(_args(assembly, tmp_path / "pick.FEM", super_element=["SEL10.IND1", "11"]))
    assert sorted(p.name for p in tmp_path.glob("*.FEM")) == ["pickT10.FEM", "pickT11.FEM"]


def test_superelement_number_is_refused_when_several_files_are_written(assembly, tmp_path):
    with pytest.raises(CliUsageError, match="each with its own number"):
        _cmd_convert(_args(assembly, tmp_path / "x.FEM", superelement=5))


def test_unknown_super_element_is_a_usage_error(assembly, tmp_path):
    with pytest.raises(CliUsageError):
        _cmd_convert(_args(assembly, tmp_path / "x.FEM", super_element=["77"]))
    with pytest.raises(CliUsageError, match="hierarchy"):
        _cmd_convert(_args(assembly, tmp_path / "x.FEM", super_element=["100"]))


def test_single_superelement_sin_keeps_its_own_number_and_the_name_given(tmp_path):
    out = tmp_path / "single.FEM"
    _cmd_convert(_args(SIN_PATH, out))
    assert [p.name for p in tmp_path.glob("*.FEM")] == ["single.FEM"]
    assert _ident_seltyp(out) == 1


# ── --merge ─────────────────────────────────────────────────────────────────────


def test_merge_writes_one_file_with_the_top_level_number(translated, tmp_path):
    out = tmp_path / "merged.FEM"
    assert _cmd_convert(_args(translated, out, merge=True)) == 0

    assert [p.name for p in tmp_path.glob("*.FEM")] == ["merged.FEM"]
    assert _ident_seltyp(out) == 100
    assert _count(out, "GCOORD") == 2 * 403
    assert _count(out, "GELMNT1") == 2 * 360

    ids = sorted(int(float(m)) for m in re.findall(r"^GCOORD\s+(\S+)", out.read_text(), re.MULTILINE))
    assert len(set(ids)) == len(ids)
    assert ids[0] == 10_000_001 and ids[-1] == 11_000_403  # type number x 10^6 + the superelement's own id


def test_merge_translates_the_second_superelement(translated, tmp_path):
    out = tmp_path / "merged.FEM"
    _cmd_convert(_args(translated, out, merge=True))
    text = out.read_text()
    first = re.search(r"^GCOORD\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", text, re.MULTILINE)
    by_id = {
        int(float(m.group(1))): tuple(float(g) for g in m.groups()[1:])
        for m in re.finditer(r"^GCOORD\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", text, re.MULTILINE)
    }
    assert first is not None
    a, b = by_id[10_000_001], by_id[11_000_001]
    assert tuple(round(y - x, 6) for x, y in zip(a, b)) == (10.0, 20.0, 30.0)


def test_merge_refuses_a_rotated_placement(assembly, tmp_path):
    with pytest.raises(CliUsageError, match="rotation"):
        _cmd_convert(_args(assembly, tmp_path / "merged.FEM", merge=True))
    assert list(tmp_path.glob("*.FEM")) == []


def test_merge_refuses_what_is_not_an_assembly(tmp_path):
    with pytest.raises(CliUsageError, match="more than one"):
        _cmd_convert(_args(SIN_PATH, tmp_path / "m.FEM", merge=True))

    deck = tmp_path / "deck.FEM"
    deck.write_text("IEND        0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n")
    with pytest.raises(CliUsageError, match="--merge"):
        _cmd_convert(_args(deck, tmp_path / "m.FEM", merge=True))


def test_merge_and_super_element_exclude_each_other(assembly, tmp_path):
    with pytest.raises(CliUsageError, match="cannot be combined"):
        _cmd_convert(_args(assembly, tmp_path / "m.FEM", merge=True, super_element=["10"]))


def test_super_element_flag_is_refused_for_other_inputs(tmp_path):
    deck = tmp_path / "deck.FEM"
    deck.write_text("IEND        0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n")
    with pytest.raises(CliUsageError, match="--super-element"):
        _cmd_convert(_args(deck, tmp_path / "o.FEM", super_element=["10"]))


# ── a .FEM input's IDENT decides the number ──────────────────────────────────────


def test_fem_input_ident_decides_the_output_number(assembly, tmp_path):
    _cmd_convert(_args(assembly, tmp_path / "src.FEM", super_element=["11"]))  # IDENT SELTYP 11, name says none
    out = tmp_path / "again.FEM"
    _cmd_convert(_args(tmp_path / "src.FEM", out))
    assert _ident_seltyp(out) == 11


def test_parser_accepts_the_new_flags():
    import ada_cli.main

    args = ada_cli.main._build_parser().parse_args(
        ["convert", "a.SIN", "b.FEM", "--merge", "--super-element", "10", "--super-element", "SEL11.IND1"]
    )
    assert args.merge is True and args.super_element == ["10", "SEL11.IND1"]

"""``from_fem`` / ``to_fem`` with ``report_file``: the conversion report written where asked.

A library caller gets the same structured record ``ada convert`` writes -- every keyword the
reader left unread, every construct the writer could not express -- without running the CLI or
opening a collector themselves.
"""

from __future__ import annotations

import json

import pytest

import ada
from ada.fem.formats import conversion_report
from ada.fem.formats.conversion_report import STATUS_COMPLETED, STATUS_WITH_OMISSIONS

DECK = """*Heading
a deck with one keyword no reader handles
*Node
1, 0., 0., 0.
2, 1., 0., 0.
3, 1., 1., 0.
*Element, type=S3, elset=plate
1, 1, 2, 3
*Material, name=steel
*Elastic
2.1e11, 0.3
*Density
7850.,
*Shell Section, elset=plate, material=steel
0.01, 5
*Radiation View Factor
"""


def _deck(tmp_path):
    path = tmp_path / "deck.inp"
    path.write_text(DECK)
    return path


def _read(path):
    return json.loads(path.read_text())


def test_from_fem_writes_what_the_reader_left_out(tmp_path):
    report = tmp_path / "out" / "read.json"
    ada.from_fem(_deck(tmp_path), "abaqus", report_file=report)

    data = _read(report)
    assert data["status"] == STATUS_WITH_OMISSIONS
    assert data["input"] == [str((tmp_path / "deck.inp").resolve())]
    assert any(f["keyword"] == "*RADIATION VIEW FACTOR" for f in data["findings"])


def test_to_fem_writes_what_the_writer_left_out(tmp_path):
    a = ada.from_fem(_deck(tmp_path), "abaqus")
    a.get_all_parts_in_assembly()[0].fem.sections[0].thickness = 0.0  # no Abaqus form
    report = tmp_path / "write.json"
    a.to_fem("m", "abaqus", scratch_dir=tmp_path, overwrite=True, report_file=report)

    data = _read(report)
    assert data["to_format"] == "abaqus"
    assert any(f["kind"] == "omitted" and f["stage"] == "abaqus writer" for f in data["findings"])


def test_a_clean_conversion_still_writes_a_report_saying_so(tmp_path):
    a = ada.from_fem(_deck(tmp_path), "abaqus")
    report = tmp_path / "clean.json"
    a.to_fem("m", "calculix", scratch_dir=tmp_path, overwrite=True, report_file=report)
    data = _read(report)
    assert data["status"] == STATUS_COMPLETED
    assert not [f for f in data["findings"] if f["kind"] != "note"]


def test_an_enclosing_collector_still_sees_the_findings(tmp_path):
    """``ada convert`` collects around the whole conversion; a report file must not take the
    findings away from it."""
    with conversion_report.collect() as outer:
        ada.from_fem(_deck(tmp_path), "abaqus", report_file=tmp_path / "r.json")
    assert any(f.keyword == "*RADIATION VIEW FACTOR" for f in outer.findings)


def test_a_conversion_that_fails_still_writes_what_it_found(tmp_path):
    bad = tmp_path / "bad.inp"
    bad.write_text(
        DECK.replace("*Element, type=S3, elset=plate\n1, 1, 2, 3", "*Element, type=S3, elset=plate\n1, 1, 2, 99")
    )
    report = tmp_path / "bad.json"
    with pytest.raises(Exception):
        ada.from_fem(bad, "abaqus", report_file=report)
    data = _read(report)
    assert "99" in data["error"]  # the element's missing node, as the exception said it
    assert isinstance(data["findings"], list)

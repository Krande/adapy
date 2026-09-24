"""Every keyword the reader does not read is reported, once per keyword, through the conversion report.

A deck may use any keyword Abaqus has, and the reader reads a few dozen. Before this, the rest
went nowhere: a local coordinate system (``*System``), a load, an amplitude or a material law the
reader has no handler for vanished without a word, and a converted model could be missing them
with nothing to say so. What counts as read is what the reader *asked for* while reading
(``lexer.track_reads``), not a list kept beside it.
"""

from __future__ import annotations

import logging
import textwrap


import ada
from ada.fem.formats import conversion_report
from ada.fem.formats.abaqus.read.reader import READER_STAGE

_MESH_ONLY = """\
*Node
1, 0., 0., 0.
2, 1., 0., 0.
3, 1., 1., 0.
4, 0., 1., 0.
5, 0., 0., 1.
*Element, type=C3D4, elset=solid
1, 1, 2, 3, 5
2, 1, 3, 4, 5
*Nset, nset=base
1, 2, 3, 4
*Solid Section, elset=solid, material=steel
,
"""
_MATERIAL = """\
*Material, name=steel
*Elastic
2.1e11, 0.3
*Density
7850.,
"""
_MESH = _MESH_ONLY + _MATERIAL


def _read(tmp_path, text: str, name: str = "deck.inp"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    with conversion_report.collect() as report:
        a = ada.from_fem(path, "abaqus")
    found = {f.keyword: f for f in report.findings if f.stage == READER_STAGE}
    return a, report, found


def test_unread_keywords_are_reported_once_each_and_read_ones_are_not(tmp_path):
    deck = (
        "*Heading\nsynthetic\n"
        "*System\n0., 0., 0., 1., 0., 0.\n"
        + _MESH
        + "*Amplitude, name=ramp\n0., 0., 1., 1.\n"
        + "*Constraint Controls, print=yes\n"
        "*Step, name=load\n*Static\n1., 1.\n"
        "*Cload\n3, 3, -1.\n"
        "*Cload\n5, 3, -1.\n"
        "*Node Output\nU,\n"
        "*End Step\n"
    )
    _, report, found = _read(tmp_path, deck)

    # what the reader reads is never reported
    for kw in ("*NODE", "*ELEMENT", "*NSET", "*MATERIAL", "*ELASTIC", "*DENSITY", "*SOLID SECTION"):
        assert kw not in found, kw

    # a local coordinate system silently ignored would move every node after it
    assert found["*SYSTEM"].kind == "omitted"
    assert found["*AMPLITUDE"].kind == "omitted"
    assert found["*STATIC"].kind == "omitted"

    # counted per keyword, not per block, with where to look
    cload = found["*CLOAD"]
    assert cload.kind == "omitted" and cload.count == 2
    assert cload.details["blocks"] == 2
    assert cload.details["first_line"] == deck.splitlines().index("*Cload") + 1

    # changes nothing in the model: a note, which does not fail --strict
    assert found["*HEADING"].kind == "note"
    assert found["*NODE OUTPUT"].kind == "note"
    assert found["*CONSTRAINT CONTROLS"].kind == "note"  # a solver setting, and adapy writes it

    assert report.has_omissions


def test_keywords_read_by_hand_matching_are_not_reported(tmp_path):
    """The readers that walk tokenize() themselves declare what they consume (mark_read): a
    material's property blocks, an interaction's friction, a coupling's *Kinematic."""
    deck = (
        _MESH + "*Surface Interaction, name=rough\n*Friction\n0.3,\n*Surface Behavior, pressure-overclosure=HARD\n"
        "*Node, nset=rp\n9, 0.5, 0.5, 2.\n"
        "*Surface, type=NODE, name=top\nbase, 1.\n"
        "*Coupling, constraint name=c1, ref node=rp, surface=top\n*Kinematic\n"
    )
    _, _, found = _read(tmp_path, deck)
    for kw in ("*SURFACE INTERACTION", "*FRICTION", "*SURFACE BEHAVIOR", "*COUPLING", "*KINEMATIC"):
        assert kw not in found, kw


def test_history_data_in_the_last_step_of_an_assembly_deck_is_reported_even_for_read_keywords(tmp_path):
    """*Boundary is read as model data -- but not from inside the step the reader cuts off, so
    one there is reported, and one before it is not."""
    deck = (
        "*Part, name=p\n" + _MESH_ONLY + "*End Part\n"
        "*Assembly, name=a\n*Instance, name=p-1, part=p\n*End Instance\n*End Assembly\n"
        + _MATERIAL
        + "*Boundary\np-1.base, ENCASTRE\n"
        "*Step, name=s\n*Static\n1., 1.\n*Boundary\np-1.base, 1, 1\n*End Step\n"
    )
    _, _, found = _read(tmp_path, deck)
    bc = found["*BOUNDARY"]
    assert bc.kind == "omitted" and bc.count == 1
    assert "history data" in bc.reason


def test_outside_a_collector_it_still_logs(tmp_path):
    """No collector, no silence: conversion_report.current() logs every finding. (A handler on
    the ``ada`` logger, not caplog: ada.config sets ``propagate = False`` on it.)"""
    from ada.config import logger

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    path = tmp_path / "deck.inp"
    path.write_text("*System\n0., 0., 0., 1., 0., 0.\n" + _MESH)
    handler = _Collect(logging.WARNING)
    logger.addHandler(handler)
    try:
        ada.from_fem(path, "abaqus")
    finally:
        logger.removeHandler(handler)
    assert any("*SYSTEM" in r.getMessage() for r in records)

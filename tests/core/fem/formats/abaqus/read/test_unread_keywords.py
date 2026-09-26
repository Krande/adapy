"""Every keyword the reader does not read is reported, once per keyword, through the conversion report.

A deck may use any keyword Abaqus has, and the reader reads a few dozen. Before this, the rest
went nowhere: a geometric imperfection (``*Imperfection``), a load, an amplitude or a material
law the reader has no handler for vanished without a word, and a converted model could be missing
them with nothing to say so. What counts as read is what the reader *asked for* while reading
(``lexer.track_reads``), not a list kept beside it.

``*System`` used to be the example here. It is read now -- see
:mod:`~ada.fem.formats.abaqus.read.read_systems`, which applies the transform and reports the one
case it cannot determine -- so an unread keyword that is still unread stands in its place.
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
        "*Imperfection, file=job, step=1\n1, 0.01\n"
        + _MESH
        + "*Amplitude, name=ramp\n0., 0., 1., 1.\n"
        + "*Constraint Controls, print=yes\n"
        "*Step, name=load\n*Static\n1., 1.\n"
        "*Temperature\n3, 20.\n"
        "*Temperature\n5, 20.\n"
        "*Cload\n3, 3, -1.\n"
        "*Node Print\nU,\n"
        "*End Step\n"
    )
    _, report, found = _read(tmp_path, deck)

    # what the reader reads is never reported
    read = ("*NODE", "*ELEMENT", "*NSET", "*MATERIAL", "*ELASTIC", "*DENSITY", "*SOLID SECTION")
    for kw in (*read, "*AMPLITUDE", "*STEP", "*STATIC", "*CLOAD"):
        assert kw not in found, kw

    # model data with no handler: reported, so the model is not quietly missing it
    assert found["*IMPERFECTION"].kind == "omitted"

    # counted per keyword, not per block, with where to look
    temp = found["*TEMPERATURE"]
    assert temp.kind == "omitted" and temp.count == 2
    assert temp.details["blocks"] == 2
    assert temp.details["first_line"] == deck.splitlines().index("*Temperature") + 1

    # changes nothing in the model: a note, which does not fail --strict
    assert found["*HEADING"].kind == "note"
    assert found["*NODE PRINT"].kind == "note"
    assert found["*CONSTRAINT CONTROLS"].kind == "note"  # a solver setting, and adapy writes it

    assert report.has_omissions


def test_keywords_read_by_hand_matching_are_not_reported(tmp_path):
    """The readers that walk tokenize() themselves declare what they consume (mark_read): a
    material's property blocks, an interaction's friction, a coupling's *Kinematic."""
    deck = (
        _MESH + "*Surface Interaction, name=rough\n*Friction\n0.3,\n*Surface Behavior, pressure-overclosure=HARD\n"
        "*Node, nset=rp\n9, 0.5, 0.5, 2.\n"
        "*Surface, type=NODE, name=top\nbase, 1.\n"
        # The *Kinematic block carries a DOF line so that the coupling reader's own note about an
        # empty one -- a finding about the constraint, not an unread keyword -- stays out of the way.
        "*Coupling, constraint name=c1, ref node=rp, surface=top\n*Kinematic\n1, 6\n"
    )
    _, _, found = _read(tmp_path, deck)
    for kw in ("*SURFACE INTERACTION", "*FRICTION", "*SURFACE BEHAVIOR", "*COUPLING", "*KINEMATIC"):
        assert kw not in found, kw


def test_history_data_is_reported_by_what_the_step_reader_reads(tmp_path):
    """Inside a step, what counts as read is the step reader's list, not the model reader's:
    *Boundary is read in both places, *Initial Conditions only as model data -- so one in a step
    is reported, and the one before the step is not."""
    deck = (
        "*Part, name=p\n" + _MESH_ONLY + "*End Part\n"
        "*Assembly, name=a\n*Instance, name=p-1, part=p\n*End Instance\n*End Assembly\n"
        + _MATERIAL
        + "*Boundary\np-1.base, ENCASTRE\n"
        "** Name: v0   Type: Velocity\n*Initial Conditions, type=VELOCITY\np-1.base, 1, 1.\n"
        "*Step, name=s\n*Static\n1., 1.\n*Boundary\np-1.base, 1, 1\n"
        "*Initial Conditions, type=VELOCITY\np-1.base, 1, 1.\n*End Step\n"
    )
    a, _, found = _read(tmp_path, deck)
    assert "*BOUNDARY" not in found and [len(s.bcs) for s in a.fem.steps] == [1]
    ic = found["*INITIAL CONDITIONS"]
    assert ic.kind == "omitted" and ic.count == 1
    assert "in a step" in ic.reason


def test_outside_a_collector_it_still_logs(tmp_path):
    """No collector, no silence: conversion_report.current() logs every finding. (A handler on
    the ``ada`` logger, not caplog: ada.config sets ``propagate = False`` on it.)"""
    from ada.config import logger

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    path = tmp_path / "deck.inp"
    path.write_text("*Imperfection, file=job, step=1\n1, 0.01\n" + _MESH)
    handler = _Collect(logging.WARNING)
    logger.addHandler(handler)
    try:
        ada.from_fem(path, "abaqus")
    finally:
        logger.removeHandler(handler)
    assert any("*IMPERFECTION" in r.getMessage() for r in records)

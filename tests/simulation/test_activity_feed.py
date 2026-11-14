"""The activity feed of a run (``ada.simulation.activity``): what each actor is doing and planning,
written a line at a time and read back -- and the role a provider stores it with."""

from __future__ import annotations

import json

import pytest

from ada.simulation import allowed_artefact_roles
from ada.simulation.activity import (
    ACTIVITY_ROLE,
    ACTIVITY_SCHEMA,
    ActivityActor,
    ActivityWriter,
    parse_activity,
)


def test_a_feed_is_written_a_whole_line_at_a_time_and_reads_back(tmp_path):
    path = tmp_path / "activity.jsonl"
    with ActivityWriter(path, [ActivityActor("mission", "Mission")]) as feed:
        feed.state(0.0, "mission", "Mission started")
        # Whole lines from the first: the file can be stored while it is being written.
        assert path.read_text(encoding="utf-8").endswith("\n")
        feed.actors([ActivityActor("amr", "Logistics robot", "#4db3e6")])
        feed.state(0.2, "amr", "Driving into the cell", data={"goal": [1.0, 2.0, 0.0]})
        feed.plan(0.2, "amr", "Dock beside the picker")
        feed.event(13.4, "picker", "View 1: no depth image arrived", level="warn")
        feed.plan(13.4, "amr", "")

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0] == {"schema": ACTIVITY_SCHEMA, "actors": [{"id": "mission", "label": "Mission"}]}
    assert lines[3] == {
        "t": 0.2,
        "actor": "amr",
        "kind": "state",
        "text": "Driving into the cell",
        "data": {"goal": [1.0, 2.0, 0.0]},
    }
    assert "level" not in lines[1] and lines[5]["level"] == "warn"

    read = parse_activity(path.read_text(encoding="utf-8"))
    # The header's actors, one named later with its label and colour, and one only an entry names.
    assert [(a.id, a.label, a.color) for a in read.actors] == [
        ("mission", "Mission", ""),
        ("amr", "Logistics robot", "#4db3e6"),
        ("picker", "", ""),
    ]
    assert [(e.t, e.actor, e.kind, e.text) for e in read.entries] == [
        (0.0, "mission", "state", "Mission started"),
        (0.2, "amr", "state", "Driving into the cell"),
        (0.2, "amr", "plan", "Dock beside the picker"),
        (13.4, "picker", "event", "View 1: no depth image arrived"),
        (13.4, "amr", "plan", ""),
    ]
    assert read.entries[1].data == {"goal": [1.0, 2.0, 0.0]} and read.entries[3].level == "warn"


def test_a_feed_that_is_still_being_written_reads_as_far_as_it_goes():
    text = (
        json.dumps({"schema": ACTIVITY_SCHEMA, "actors": []})
        + "\n"
        + json.dumps({"t": 1, "actor": "a", "text": "kept"})
        + "\nnot json\n"
        + json.dumps({"actor": "a", "text": "no time"})
        + "\n"
        + '{"t": 2, "actor": "a", "text": "cut sho'
    )
    read = parse_activity(text)
    assert [e.text for e in read.entries] == ["kept"]
    assert read.entries[0].kind == "event" and read.entries[0].level == "info"


def test_what_is_not_a_feed_is_refused_and_so_is_an_entry_of_no_known_kind(tmp_path):
    for not_a_feed in ("", "glTF", json.dumps({"schema": "something/1"})):
        with pytest.raises(ValueError, match="activity feed"):
            parse_activity(not_a_feed)
    with ActivityWriter(tmp_path / "a.jsonl") as feed:
        with pytest.raises(ValueError, match="kind"):
            feed.add(0.0, "a", "x", kind="thought")
        with pytest.raises(ValueError, match="level"):
            feed.add(0.0, "a", "x", level="loud")


def test_a_run_of_any_domain_may_store_an_activity_feed():
    for domain in ("fea", "robotics", "cfd"):
        assert ACTIVITY_ROLE in allowed_artefact_roles(domain)

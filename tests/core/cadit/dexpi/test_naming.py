"""``unique_name`` reproduces, output for output, the five helpers it replaced.

The expected lists below were recorded from the former helpers -- ``to_procedural._unique_name``,
``equipment_list._unique_slug``, ``from_ada._dedupe``, ``nozzle_placers._unique_names`` and the
loop inside ``to_procedural._equipment_names`` -- on the same probing input before any of them
was removed. The input has repeats, two empty candidates, and a candidate that collides with a
name the numbering itself generated.
"""

from types import SimpleNamespace

import pytest

from ada.cadit.dexpi.nozzle_placers import NozzleSpec, port_names
from ada.cadit.dexpi.read.naming import unique_name

PROBE = ["V-1", "V-1", "V-1", "", "", "V-1-2", "V-1"]


def _run(**kwargs) -> list[str]:
    taken: set[str] = set()
    return [unique_name(candidate, taken, **kwargs) for candidate in PROBE]


def test_equipment_and_slug_pools_fall_back_to_equipment_and_number_the_raw_candidate():
    # to_procedural._unique_name and equipment_list._unique_slug, which were identical.
    assert _run(fallback="equipment") == ["V-1", "V-1-2", "V-1-3", "equipment", "-2", "V-1-2-2", "V-1-4"]


def test_the_writer_pool_keeps_an_empty_candidate_empty():
    # from_ada._dedupe, and the loop in to_procedural._equipment_names (whose candidate is never
    # empty, so the two agree on every name the writer has to find again).
    assert _run() == ["V-1", "V-1-2", "V-1-3", "", "-2", "V-1-2-2", "V-1-4"]


def test_port_names_fall_back_to_port_and_number_the_resolved_name():
    # nozzle_placers._unique_names, reached through the public port_names.
    specs = [NozzleSpec(id=f"N{i}", name=name) for i, name in enumerate(PROBE)]
    assert list(port_names(specs).values()) == ["V-1", "V-1-2", "V-1-3", "port", "port-2", "V-1-2-2", "V-1-4"]
    assert _run(fallback="port", style="on-name") == list(port_names(specs).values())


def test_a_preseeded_pool_is_respected_and_claimed():
    taken = {"V-1", "V-1-2"}
    assert unique_name("V-1", taken) == "V-1-3"
    assert taken == {"V-1", "V-1-2", "V-1-3"}


def test_equipment_names_over_resolved_entries_match_the_former_loop():
    from ada.cadit.dexpi.read.to_procedural import _equipment_names

    tags = ["V-1", "V-1", " V-1 ", None, "", "V-1-2"]
    resolved = [SimpleNamespace(item=SimpleNamespace(tag=tag), slug=f"slug{i}") for i, tag in enumerate(tags)]
    assert _equipment_names(resolved) == {
        "slug0": "V-1",
        "slug1": "V-1-2",
        "slug2": "V-1-3",
        "slug3": "slug3",
        "slug4": "slug4",
        "slug5": "V-1-2-2",
    }


@pytest.mark.parametrize("style", ["on-candidate", "on-name"])
def test_the_styles_only_differ_for_an_empty_candidate(style):
    taken: set[str] = set()
    assert [unique_name("A", taken, fallback="x", style=style) for _ in range(3)] == ["A", "A-2", "A-3"]

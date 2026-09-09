"""The DEXPI round-trip claims (T1-T4) -- see the plan's "Roundtrip strategy" for why there are four
of them and why none is byte identity.

**T1, lossless XML echo** -- ``file -> doc -> XML -> doc'`` under
:func:`~ada.cadit.dexpi.canonical.canonicalize`. Already covered, per fixture and per writer, by
``test_write_proteus.py``/``test_write_dexpi20.py``; not duplicated here.

**T2, adapy round trip with sidecar** -- ``ada.from_dexpi(...) -> Assembly.to_dexpi(...) -> read
again``. Two claims: an *unedited* assembly is canonically equal to the source (this module's
``test_t2_unedited_*``), and an edit made on the live assembly shows up as *exactly that delta*
-- compared item by item, not by a diff count -- and nothing else moves
(``test_t2_edited_add_and_remove_a_port``).

**T3, pure-adapy write** -- ``Assembly.to_dexpi(from_scratch=True)`` from a plain assembly with no
DEXPI provenance at all. Lossy by construction: only what a live ``ada.Equipment``/``System`` carries
survives. Asserted by re-parsing and comparing the equipment/port/system graph, never by canonical
equality with anything -- there is no source document to be equal to.

**T4, cross-flavour** -- already covered by ``test_convergence.py``
(``graph_signature`` equality between a Proteus file and its DEXPI 2.0 twin) and by construction in
``test_fixtures_current.py`` (both flavours of every generated fixture come from the one
``DexpiDocument`` the example generator builds). Not duplicated here either.
"""

from __future__ import annotations

import pytest

import ada
from ada.api.systems import PipingSystem, Port, PortDirection
from ada.cadit.dexpi import canonicalize, read_dexpi
from ada.cadit.dexpi.model import ItemKind
from ada.cadit.dexpi.read.to_procedural import DexpiImportReport
from ada.cadit.dexpi.write.from_ada import _segment_name as segment_name
from ada.cadit.dexpi.write.from_ada import build_from_scratch

# The generated fixtures plus the two vendored official files -- the same four T1 is held to in
# test_write_proteus.py, now carried through a live Assembly rather than a bare DexpiDocument.
#
# ``unit_separator_proteus.xml`` is not in this tuple. Its two ``PipeTee``\ s no longer cost it
# anything -- they import as branch-point equipment and write back as themselves (see
# ``test_branch_points.py``) -- but one of its lines still cannot round-trip: ``205/1`` is a relief
# valve discharging to something the P&ID does not draw, so it has a single end and nothing to route
# to. A one-ended run is not a gap a write-back path can or should paper over. It gets its own,
# weaker test below: what *does* reach the live assembly must still round-trip exactly, and what does
# not must be the same set the import already reported.
T2_FILES = (
    "tiny_two_equipment_proteus.xml",
    "vendor/P01V01-VER.EX01.xml",
    "vendor/E01V02-VER.EX01.xml",
)

# Generous enough that nothing in any of the four files above is too large to place -- T2 is a claim
# about the merge writer, and the read no longer has deck bounds to get wrong: layout is a
# property of a *build*, and nothing here builds.


@pytest.fixture
def dexpi_files(example_files):
    return example_files / "dexpi_files"


def _clean_import(path, **kwargs) -> tuple[ada.SystemModel, DexpiImportReport]:
    """``ada.SystemModel.from_dexpi(path, ...)`` plus its report.

    The model *is* the shape the write-back path wants -- equipment, ports and systems with no
    coordinates -- which is why the export lives there and not on a built assembly: DEXPI has no way
    to express a placement, so nothing a build produces could be written back anyway.
    """
    model = ada.SystemModel.from_dexpi(path, **kwargs)
    return model, model.report


# --------------------------------------------------------------------------- #
# from_scratch=False with no source document
# --------------------------------------------------------------------------- #
def test_merge_without_a_source_document_raises_pointing_at_from_scratch():
    model = ada.SystemModel(name="no-provenance")
    assert model.source_document is None

    with pytest.raises(ValueError, match="from_scratch=True"):
        model.to_dexpi("unused.xml")


# --------------------------------------------------------------------------- #
# T2, unedited
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", T2_FILES)
def test_t2_unedited_assembly_round_trips_to_the_source(name, dexpi_files, tmp_path):
    source = read_dexpi(dexpi_files / name)
    model, report = _clean_import(dexpi_files / name)
    # Only connectivity matters here -- an "unplaced" equipment (a layout concern, moot for the
    # roomy bounds above) would still be a real assembly gap, but a dropped *system* would mean the
    # merge is being asked to reconstruct wiring the live assembly never actually held.
    assert not report.of_kind("system"), report.format()

    written = model.to_dexpi(tmp_path / name)
    again = read_dexpi(written)

    assert again.warnings == []
    assert canonicalize(again) == canonicalize(source)


def _connection_tuples(doc, owner_ids: set[str]) -> list[tuple[str, str, str, str, str]]:
    return sorted(
        (c.from_item or "", c.from_node or "", c.to_item or "", c.to_node or "", c.owner_id or "")
        for c in doc.connections
        if c.owner_id in owner_ids
    )


def test_t2_unedited_unit_separator_keeps_everything_the_import_kept(dexpi_files, tmp_path):
    """The weaker claim for the one fixture the importer cannot fully wire (see ``T2_FILES``): every
    equipment item is untouched, and every segment the import *did* turn into a system keeps both its
    own item record and its connections exactly; only the ones the report already named as lost --
    and their own descendants and connections -- are gone."""
    path = dexpi_files / "unit_separator_proteus.xml"
    source = read_dexpi(path)
    model, report = _clean_import(path)
    assert not report.of_kind("equipment"), report.format()
    dropped_names = {issue.name for issue in report.of_kind("system")}
    assert dropped_names, "this fixture is only interesting while something in it is still dropped"

    again = read_dexpi(model.to_dexpi(tmp_path / "unit_separator.xml"))
    assert again.warnings == []

    kept_segments = {
        segment.id
        for segment in source.by_kind(ItemKind.PIPING_SEGMENT)
        if segment_name(source, segment) not in dropped_names
    }
    before = {item["id"]: item for item in canonicalize(source)["items"]}
    after = {item["id"]: item for item in canonicalize(again)["items"]}

    for segment_id in kept_segments:
        assert after.get(segment_id) == before[segment_id], segment_id
    for item in source.by_kind(ItemKind.EQUIPMENT):
        assert after.get(item.id) == before[item.id], item.id
    assert _connection_tuples(again, kept_segments) == _connection_tuples(source, kept_segments)


# --------------------------------------------------------------------------- #
# T2, edited -- exactly the delta, and nothing else
# --------------------------------------------------------------------------- #
def _nozzle_tags(doc, equipment_id: str) -> set[str]:
    return {
        doc.items[child_id].tag
        for child_id in doc.items[equipment_id].child_ids
        if doc.items[child_id].kind is ItemKind.NOZZLE
    }


def test_t2_edited_add_and_remove_a_port(dexpi_files, tmp_path):
    """``T-100`` (``Tank-1``) starts with two nozzles: ``N1`` (connected, DN80) and ``N2`` (an
    unconnected DN50 inlet). The live equipment loses ``N2`` and gains a new port ``n3`` -- and that
    is the only thing that may show up as different in the re-read document.
    """
    path = dexpi_files / "tiny_two_equipment_proteus.xml"
    source = read_dexpi(path)
    model, report = _clean_import(path)
    assert report.is_clean, report.format()

    tank = next(eq for eq in model.equipment if eq.name == "T-100")
    assert {p.name for p in tank.ports} == {"n1", "n2"}

    tank.ports = [p for p in tank.ports if p.name != "n2"]
    tank.add_port(Port("n3", (1.0, 1.0, 2.0), (0.0, 0.0, 1.0), PortDirection.OUT, "process"))

    again = read_dexpi(model.to_dexpi(tmp_path / "edited.xml"))

    tank_id = next(item.id for item in source.items.values() if item.tag == "T-100")
    assert _nozzle_tags(source, tank_id) == {"N1", "N2"}
    assert _nozzle_tags(again, tank_id) == {"N1", "n3"}

    before = {item["id"]: item for item in canonicalize(source)["items"]}
    after = {item["id"]: item for item in canonicalize(again)["items"]}

    removed = set(before) - set(after)
    added = set(after) - set(before)
    changed = {item_id for item_id in before.keys() & after.keys() if before[item_id] != after[item_id]}

    # Exactly one nozzle item disappears (N2) and exactly one appears (n3); the only *existing* item
    # whose own record changes is the tank itself, because its child list now names a different
    # nozzle -- not, say, the pump, the valve or the segment three items over.
    assert {source.items[item_id].tag for item_id in removed} == {"N2"}
    assert {again.items[item_id].tag for item_id in added} == {"n3"}
    assert changed == {tank_id}
    # And the connectivity graph -- N2 was never wired to anything -- is untouched byte for byte.
    assert canonicalize(again)["connections"] == canonicalize(source)["connections"]


# --------------------------------------------------------------------------- #
# T3, from_scratch
# --------------------------------------------------------------------------- #
def _archetype_model() -> ada.Assembly:
    """A plain two-equipment, one-system model with no DEXPI provenance whatsoever -- the shape
    ``ada.topo_model`` archetypes produce, built directly here so this test does not depend on the
    layout/compile pipeline to make its point."""
    tank = ada.Equipment(
        "TK-01",
        mass=1000.0,
        cog=(0.0, 0.0, 1.0),
        origin=(0.0, 0.0, 0.0),
        lx=2.0,
        ly=2.0,
        lz=2.0,
        ports=[Port("outlet", (1.0, 0.0, 0.2), (1.0, 0.0, 0.0), PortDirection.OUT, "process")],
    )
    pump = ada.Equipment(
        "PU-01",
        mass=200.0,
        cog=(0.0, 0.0, 0.5),
        origin=(5.0, 0.0, 0.0),
        lx=1.0,
        ly=1.0,
        lz=1.0,
        ports=[Port("suction", (-0.5, 0.0, 0.5), (-1.0, 0.0, 0.0), PortDirection.IN, "process")],
    )
    system = PipingSystem("100/1", medium="PW")
    system.connect(tank, "outlet").connect(pump, "suction")
    return ada.SystemModel(name="archetype", equipment=[tank, pump], systems=[system])


def test_t3_from_scratch_parses_back_and_keeps_the_ada_owned_graph(tmp_path):
    model = _archetype_model()
    assert model.source_document is None

    written = model.to_dexpi(tmp_path / "from_scratch.xml", from_scratch=True)
    doc = read_dexpi(written)
    assert doc.warnings == []

    equipment = {item.tag: item for item in doc.items.values() if item.kind is ItemKind.EQUIPMENT}
    assert set(equipment) == {"TK-01", "PU-01"}

    ports = {item.tag: {n.tag for n in item.process_nodes} for item in equipment.values()}
    assert ports == {"TK-01": {"outlet"}, "PU-01": {"suction"}}

    assert len(doc.connections) == 1
    connection = doc.connections[0]
    from_tag = doc.find_node(connection.from_node)[0].tag
    to_tag = doc.find_node(connection.to_node)[0].tag
    assert {from_tag, to_tag} == {"TK-01", "PU-01"}


def test_t3_never_claims_losslessness():
    """The one thing this path must never say -- checked in the docstrings that describe it."""
    assert "lossy" in ada.SystemModel.to_dexpi.__doc__.lower()
    assert "lossy" in build_from_scratch.__doc__.lower()
    for word in ("lossless", "loss-free", "no data is lost"):
        assert word not in build_from_scratch.__doc__.lower()

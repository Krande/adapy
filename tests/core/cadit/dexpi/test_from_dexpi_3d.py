"""The headline: a DEXPI file in, a routed 3D model out.

Everything the branch exists for meets here. ``ada.from_dexpi`` reads the P&ID, resolves each item
to a physical equipment definition, generates the decks, wires one system per DEXPI segment, routes
them over the model grid, models the penetrations where a run crosses a built deck, and hands back
an :class:`ada.Assembly` that exports.

The assertions that matter are the ones about *absence*. The procedural compiler is deliberately
forgiving -- ``_wire_systems`` drops an unwireable system with a ``logger.warning`` and
``run_design(skip_failed=True)`` skips an unroutable run the same way -- so an import can come back
looking complete with half the pipes missing. This test therefore asserts three things at once:
every segment produced routed geometry, the import report is empty, and no warning about a skipped
system was logged. Any one of them alone can be satisfied by a model that quietly lost a run.
"""

from __future__ import annotations

import contextlib
import logging

import pytest

import ada
from ada.topo_model.build_spec import ProceduralBuildSpec
from ada.topo_model.layout import LayoutRules, validate_equipment_in_cells

from .test_to_procedural import (
    UNIT_EQUIPMENT,
    UNIT_LAYOUT,
    UNIT_SYSTEMS,
    write_unit_pid,
)


@contextlib.contextmanager
def captured_warnings():
    """Warnings from adapy's own logger. ``configure_logger`` turns propagation off, so ``caplog``
    -- which listens on the root -- never sees them; this attaches to the ``ada`` logger itself."""
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("ada")
    handler = _Collector(level=logging.WARNING)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


UNIT_SPEC = ProceduralBuildSpec(layout=UNIT_LAYOUT)


@pytest.fixture(scope="module")
def unit_path(tmp_path_factory):
    return write_unit_pid(tmp_path_factory.mktemp("dexpi"))


@pytest.fixture(scope="module")
def model(unit_path):
    """The P&ID read into the native model -- no coordinates, nothing built."""
    return ada.SystemModel.from_dexpi(unit_path)


@pytest.fixture(scope="module")
def built(model):
    """That model built all the way to a routed assembly, once for the whole module."""
    with captured_warnings() as records:
        assembly = model.to_assembly(UNIT_SPEC)
    return assembly, [record.getMessage() for record in records]


def _routed_runs(assembly: ada.Assembly) -> set[str]:
    """The system names that produced geometry, from the ``Systems`` part of the built model.

    Presence of geometry is the check rather than the compiler's log lines: a run is in the model
    or it is not, and that survives any rewording of the warnings.
    """
    out: set[str] = set()
    for part in assembly.get_all_parts_in_assembly(include_self=True):
        if part.name == "Systems":
            out.update(str(obj.name).rsplit("_route", 1)[0] for obj in part.get_all_physical_objects())
    return out


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #
def test_every_equipment_is_placed_in_a_cell(model, built):
    assembly, _ = built
    equipment = {eq.name for eq in assembly.get_all_parts_in_assembly() if isinstance(eq, ada.Equipment)}
    assert equipment == set(UNIT_EQUIPMENT)

    doc, _catalog = ada.dexpi_to_procedural(model.metadata["source"], layout=UNIT_LAYOUT)
    assert validate_equipment_in_cells(doc) == []
    assert sorted({row["SPACE_NAME"] for row in doc["equipments"]}) == ["Deck1", "Deck2"]


def test_every_piping_network_segment_produced_a_routed_run(built):
    assembly, _ = built
    assert _routed_runs(assembly) == set(UNIT_SYSTEMS)


def test_nothing_was_dropped_and_nothing_was_skipped(model, built):
    """Asserted three ways -- the read report, the build report, and the geometry -- because each on
    its own can be satisfied by a model that lost a run.

    The two reports are separate on purpose: "the P&ID never said where this run goes" is a reading
    failure and "the router could not find a path" is a building one, and they have different fixes.
    """
    assembly, messages = built

    assert model.report.is_clean, model.report.format()
    assert model.report.stats == {"equipment": 4, "systems": 7}
    assert assembly.metadata["build"]["issues"] == []

    skipped = [m for m in messages if "skipping system" in m or "no route found" in m]
    assert skipped == []


def test_the_runs_cross_the_deck_and_get_their_penetrations(built):
    """Both pumps sit on the deck above the vessel that feeds them, so four of the seven runs cross
    a built deck plate and each of those needs its cutout."""
    assembly, _ = built
    penetrations = [p for p in assembly.get_all_parts_in_assembly() if p.name == "Penetrations"]
    assert penetrations, "runs cross a built deck; they must be sleeved"
    assert list(penetrations[0].get_all_physical_objects())


def test_the_structure_and_the_ports_are_really_there(built):
    """A routed model with no steel and no nozzles would pass every count above."""
    assembly, _ = built
    assert list(assembly.get_all_physical_objects(by_type=ada.Beam))
    ports = [port for eq in assembly.get_all_parts_in_assembly() if isinstance(eq, ada.Equipment) for port in eq.ports]
    assert len(ports) == 11
    assert all(port.category == "process" for port in ports)


def test_the_assembly_exports_to_ifc(built, tmp_path):
    assembly, _ = built
    destination = tmp_path / "separator_unit.ifc"
    assembly.to_ifc(destination, validate=False)
    assert destination.stat().st_size > 0


# --------------------------------------------------------------------------- #
# The other entry points
# --------------------------------------------------------------------------- #
def test_reading_alone_gives_the_native_model_without_coordinates(tmp_path):
    """The write-back path's model: the same equipment with the same ports and the same wiring,
    with no structure, no grid and no routing."""
    path = write_unit_pid(tmp_path)
    model = ada.SystemModel.from_dexpi(path)

    assert {eq.name for eq in model.equipment} == set(UNIT_EQUIPMENT)
    assert sorted(system.name for system in model.systems) == sorted(UNIT_SYSTEMS)
    assert all(system.routed_path is None for system in model.systems)
    # The whole point of the layer: a P&ID states no coordinate, so neither does this. ``origin``
    # is the box's base centre, so an unplaced equipment sits at its own half-extents with the
    # corner the layout would have set still zero.
    assert all(tuple(eq.origin) == (eq.lx / 2.0, eq.ly / 2.0, 0.0) for eq in model.equipment)


def test_the_intermediate_document_is_the_useful_seam(tmp_path):
    path = write_unit_pid(tmp_path)
    doc, catalog = ada.dexpi_to_procedural(path, layout=UNIT_LAYOUT)

    from ada.topo_model.builder import ProceduralBuilder

    builder = ProceduralBuilder.from_dict(doc, equipment_resolver=catalog.get)
    assert [space.NAME for space in builder.spaces] == ["Deck1", "Deck2"]
    assert sorted(system.NAME for system in builder.systems) == sorted(UNIT_SYSTEMS)


def test_bounds_too_small_is_a_build_gap_not_a_read_one(tmp_path):
    """Whether the separator fits depends entirely on the deck bounds, and the read has none.

    This is the split doing its job: the *same* P&ID reads perfectly cleanly and then fails to
    build, and the failure is reported against the rules that actually caused it.
    """
    path = write_unit_pid(tmp_path)
    model = ada.SystemModel.from_dexpi(path)
    assert model.report.is_clean, model.report.format()

    cramped = ProceduralBuildSpec(layout=LayoutRules(max_length=4.0, max_width=4.0, deck_height=5.0))
    assembly = model.to_assembly(cramped)

    layout_issues = [i for i in assembly.metadata["build"]["issues"] if i["stage"] == "layout"]
    assert layout_issues, assembly.metadata["build"]

    # And the roomy build of the same model has none, so the assertion above is not vacuous.
    assert [i for i in model.to_assembly(UNIT_SPEC).metadata["build"]["issues"] if i["stage"] == "layout"] == []


# -- what the wider official corpus insisted on ---------------------------------------------------
#
# Running ada.from_dexpi over all 220 files of the official TrainingTestCases (see
# scripts/fetch_dexpi_testcases.py) raised ValueError on 72 of them. Both causes are reduced to
# inline fixtures here, so CI holds them without the git-ignored corpus.

LEGACY_CLASSED_PID = """<?xml version="1.0" encoding="utf-8"?>
<PlantModel>
  <PlantInformation OriginatingSystem="Test Kit" SchemaVersion="4.1.1" Units="mm"/>
  <Equipment ID="Equipment-1" TagName="T4750" ComponentClass="VerticalDrums"
             ComponentName="Beh m gewoelbten Boeden">
    <Nozzle ID="Nozzle-1" TagName="N1" ComponentClass="Nozzle">
      <ConnectionPoints NumPoints="1">
        <Node ID="Nozzle-1-Node-1"><Position><Location X="0" Y="0" Z="0"/></Position></Node>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
  <Equipment ID="Equipment-2" TagName="P4711" ComponentClass="Pumps"
             ComponentName="Verdraengungspumpe allgemein">
    <Nozzle ID="Nozzle-2" TagName="N1" ComponentClass="Nozzle">
      <ConnectionPoints NumPoints="1">
        <Node ID="Nozzle-2-Node-1"><Position><Location X="0" Y="0" Z="0"/></Position></Node>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
</PlantModel>
"""

NO_EQUIPMENT_PID = """<?xml version="1.0" encoding="utf-8"?>
<PlantModel>
  <PlantInformation OriginatingSystem="Test Kit" SchemaVersion="4.1.1" Units="mm"/>
  <ProcessInstrumentationFunction ID="PIF-1" TagName="FI 11"
                                  ComponentClass="ProcessInstrumentationFunction"/>
</PlantModel>
"""


def test_equipment_classed_out_of_a_vendor_symbol_library_is_still_equipment(tmp_path):
    """``ComponentClass="VerticalDrums"``/``"Pumps"`` -- what a DEXPI 1.2 export actually writes.

    The vendored class table is generated from the DEXPI 2.0.0 specification, so
    ``is_a(cls, "ProcessEquipment")`` is False for every one of these and a P&ID full of equipment
    resolved to no equipment at all. The Proteus ``<Equipment>`` tag is the emitter's statement of
    intent and is honoured when the class is not recognisable.
    """
    path = tmp_path / "legacy.xml"
    path.write_text(LEGACY_CLASSED_PID, encoding="utf-8")

    a = ada.from_dexpi(path)

    assert sorted(e.name for e in a.get_all_parts_in_assembly() if isinstance(e, ada.Equipment)) == [
        "P4711",
        "T4750",
    ]


def test_a_chamber_is_not_promoted_by_the_tag_that_identifies_its_owner(tmp_path):
    """Proteus spells a chamber ``<Equipment ComponentClass="Chamber">``.

    The tag rule above must not turn a separator's boot into a plant asset of its own; it stays
    folded into its owner, which is what the class-based rule already did.
    """
    path = tmp_path / "chamber.xml"
    path.write_text(
        LEGACY_CLASSED_PID.replace(
            '<Equipment ID="Equipment-2" TagName="P4711" ComponentClass="Pumps"\n'
            '             ComponentName="Verdraengungspumpe allgemein">',
            '<Equipment ID="Equipment-2" TagName="boot" ComponentClass="Chamber">',
        ),
        encoding="utf-8",
    )
    doc = ada.dexpi_to_procedural(path)[0]

    names = {eq.get("NAME") or eq.get("name") for eq in (doc.get("equipments") or [])}
    assert "boot" not in names


def test_a_pid_with_nothing_to_lay_out_is_reported_not_raised(tmp_path):
    """An instrumentation-only sheet has no equipment, so the generated layout has no decks.

    ``ProceduralBuilder`` rightly refuses to compile an empty document, but that ValueError reached
    the caller for 43 of the 220 official files -- for drawings adapy had read perfectly well. It is
    a property of the model, so the build reports it and hands back an empty assembly.
    """
    path = tmp_path / "instrumentation_only.xml"
    path.write_text(NO_EQUIPMENT_PID, encoding="utf-8")

    model = ada.SystemModel.from_dexpi(path)
    assert model.equipment == []
    assert model.report.is_clean, "nothing failed to *read*; there is simply nothing to place"

    assembly = model.to_assembly()

    issues = assembly.metadata["build"]["issues"]
    assert [issue["kind"] for issue in issues] == ["model"]
    assert issues[0]["stage"] == "layout"
    assert "no equipment resolved" in issues[0]["reason"]


def test_a_pid_with_nothing_to_lay_out_still_reads_cleanly(tmp_path):
    """The half of the split that is easy to lose: a drawing adapy parsed perfectly must not be
    reported as a failed *read* just because it cannot be built."""
    path = tmp_path / "instrumentation_only.xml"
    path.write_text(NO_EQUIPMENT_PID, encoding="utf-8")

    # strict is a read argument, and the read succeeded -- so this must not raise.
    model = ada.SystemModel.from_dexpi(path, strict=True)
    assert model.systems == []


# -- feeding the router's failures back into the layout -------------------------------------------


def test_relocate_is_off_by_default_and_records_nothing(model):
    """A relocation moves where equipment stands, so it stays an explicit choice."""
    assembly = model.to_assembly(UNIT_SPEC)

    assert "relocations" not in assembly.metadata["build"]


def test_relocate_records_every_move_it_applied(model):
    """The generated layout packs on footprint alone and cannot know whether the runs will route;
    ``propose_relocations`` knows exactly which moves would clear a failed run, and nothing fed that
    back. With ``relocate=True`` the loop closes, and what it did is readable afterwards."""
    assembly = model.to_assembly(UNIT_SPEC.with_(relocate=True))

    record = assembly.metadata["build"]["relocations"]
    assert set(record) == {"applied", "unresolved", "baseline_problems", "passes"}
    assert record["passes"] >= 1
    for move in record["applied"]:
        assert move["equipment"] and move["from"] != move["to"]
        assert move["fixes"], "a recorded move must name the runs it was made for"


def test_relocate_never_loses_a_system_that_already_routed(model):
    """The loop must not trade one cleared run for another broken one.

    Both builds come from the same model, which is the point of the split: nothing is re-read, so
    the only difference between them is the build rules.
    """
    before = model.to_assembly(UNIT_SPEC)
    after = model.to_assembly(UNIT_SPEC.with_(relocate=True))

    assert _routed_runs(after) >= _routed_runs(before)

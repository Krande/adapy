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
from ada.cadit.dexpi.read.to_procedural import DexpiImportReport, dexpi_import_report
from ada.topo_model.layout import validate_equipment_in_cells

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


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The unit P&ID all the way to a routed assembly, built once for the whole module."""
    path = write_unit_pid(tmp_path_factory.mktemp("dexpi"))
    with captured_warnings() as records:
        assembly = ada.from_dexpi(path, layout=UNIT_LAYOUT)
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
def test_every_equipment_is_placed_in_a_cell(built):
    assembly, _ = built
    equipment = {eq.name for eq in assembly.get_all_parts_in_assembly() if isinstance(eq, ada.Equipment)}
    assert equipment == set(UNIT_EQUIPMENT)

    doc, _catalog = ada.dexpi_to_procedural(assembly.metadata["dexpi"]["source"], layout=UNIT_LAYOUT)
    assert validate_equipment_in_cells(doc) == []
    assert sorted({row["SPACE_NAME"] for row in doc["equipments"]}) == ["Deck1", "Deck2"]


def test_every_piping_network_segment_produced_a_routed_run(built):
    assembly, _ = built
    assert _routed_runs(assembly) == set(UNIT_SYSTEMS)


def test_nothing_was_dropped_and_nothing_was_skipped(built):
    """Risk 5, asserted three ways -- the report, the log, and the geometry -- because each on its
    own can be satisfied by a model that lost a run."""
    assembly, messages = built

    report = DexpiImportReport.from_dict(assembly.metadata["dexpi"]["report"])
    assert report.is_clean, report.format()
    assert report.stats == {"equipment": 4, "systems": 7}

    skipped = [m for m in messages if "skipping system" in m or "no route found" in m]
    assert skipped == []
    assert dexpi_import_report(assembly).startswith("DEXPI import complete")


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
def test_build_3d_false_returns_the_schematic_only_assembly(tmp_path):
    """The write-back path's model: the same equipment with the same ports and the same wiring,
    with no structure, no grid and no routing."""
    path = write_unit_pid(tmp_path)
    assembly = ada.from_dexpi(path, build_3d=False, layout=UNIT_LAYOUT)

    assert {eq.name for eq in assembly.get_all_parts_in_assembly() if isinstance(eq, ada.Equipment)} == set(
        UNIT_EQUIPMENT
    )
    assert sorted(system.name for system in assembly.systems) == sorted(UNIT_SYSTEMS)
    assert not list(assembly.get_all_physical_objects(by_type=ada.Beam))
    assert all(system.routed_path is None for system in assembly.systems)


def test_the_intermediate_document_is_the_useful_seam(tmp_path):
    path = write_unit_pid(tmp_path)
    doc, catalog = ada.dexpi_to_procedural(path, layout=UNIT_LAYOUT)

    from ada.topo_model.builder import ProceduralBuilder

    builder = ProceduralBuilder.from_dict(doc, equipment_resolver=catalog.get)
    assert [space.NAME for space in builder.spaces] == ["Deck1", "Deck2"]
    assert sorted(system.NAME for system in builder.systems) == sorted(UNIT_SYSTEMS)


def test_strict_raises_on_anything_that_did_not_reach_the_model(tmp_path):
    """The bounds are too small for the separator, so it cannot be placed. ``strict=False`` reports
    it; ``strict=True`` refuses to hand back a half-built model at all."""
    path = write_unit_pid(tmp_path)
    cramped = {"max_length": 4.0, "max_width": 4.0, "deck_height": 5.0}

    with pytest.raises(ValueError, match="did not reach the 3D model"):
        ada.from_dexpi(path, layout=cramped, strict=True)

    assembly = ada.from_dexpi(path, layout=cramped, build_3d=False)
    assert not DexpiImportReport.from_dict(assembly.metadata["dexpi"]["report"]).is_clean

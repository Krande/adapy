"""Instrumentation as 3D objects: instruments, their signal ports, and the lines between them.

A P&ID's instrumentation is connectivity, not decoration -- it is what says *this controller drives
that valve* -- and none of it used to reach the 3D model. Instruments were metadata echoed back out
by the writer, so a model built from a P&ID contained no controller, no actuator, and nothing
joining them. That is a specific, checkable way for the model to be untruthful about its source.

Three things are asserted here that are easy to get individually right and collectively wrong:

* the instrument exists as a placed object with the IFC class its DEXPI role implies;
* its ports are ``signal``, not ``process`` -- ``System.connect`` refuses a category mismatch, so a
  signal run wired to a process port is dropped by the compiler with only a warning;
* the run between two instruments is a real two-ended system, which needs the *sensing* and
  *acting* halves of one loop to stay two objects rather than folding into the loop that owns them.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

import pytest

import ada
from ada.cadit.dexpi.equipment_list import (
    instrument_items,
    operated_component,
    signal_lines,
    signal_terminal,
)
from ada.cadit.dexpi.read import read_proteus

#: Module-scoped fixtures cannot take the function-scoped ``example_files`` fixture.
_UNIT_SEPARATOR = pathlib.Path(__file__).resolve().parents[4] / "files" / "dexpi_files" / "unit_separator_proteus.xml"

# A controller whose loop owns both halves -- the element that senses and the one that acts -- with
# a signal line between them. This is the shape of the official ``C01`` file's instrumentation, and
# the shape that folding a nested instrument into its owner destroys.
LOOP_PID = """<?xml version="1.0" encoding="utf-8"?>
<PlantModel>
  <PlantInformation OriginatingSystem="Test Kit" SchemaVersion="4.1.1" Units="mm"/>
  <Equipment ID="Equipment-1" ComponentClass="Tank">
    <GenericAttributes Set="DexpiAttributes" Number="1">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="T-100"/>
    </GenericAttributes>
    <Nozzle ID="Nozzle-1" ComponentClass="Nozzle">
      <ConnectionPoints NumPoints="1">
        <Node ID="Nozzle-1-Node-1"><Position><Location X="0" Y="0" Z="0"/></Position></Node>
      </ConnectionPoints>
    </Nozzle>
  </Equipment>
  <ProcessInstrumentationFunction ID="PIF-1" ComponentClass="ProcessInstrumentationFunction">
    <GenericAttributes Set="DexpiAttributes" Number="1">
      <GenericAttribute Name="TagNameAssignmentClass" Format="string" Value="LIC-100"/>
    </GenericAttributes>
    <ProcessSignalGeneratingFunction ID="PIF-1_SE001" ComponentClass="ProcessSignalGeneratingFunction"/>
    <SignalConveyingFunction ID="SIG-1" ComponentClass="SignalConveyingFunction">
      <Association Type="has logical start" ItemID="PIF-1_SE001"/>
      <Association Type="has logical end" ItemID="PIF-1"/>
    </SignalConveyingFunction>
  </ProcessInstrumentationFunction>
</PlantModel>
"""


@pytest.fixture(scope="module")
def loop_pid(tmp_path_factory):
    path = tmp_path_factory.mktemp("dexpi_instr") / "loop.xml"
    path.write_text(LOOP_PID, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def loop_doc():
    return read_proteus(ET.fromstring(LOOP_PID))


@pytest.fixture(scope="module")
def loop_model(loop_pid):
    return ada.SystemModel.from_dexpi(loop_pid)


@pytest.fixture(scope="module")
def built(loop_model):
    return loop_model.to_assembly()


def _equipment(assembly):
    return {eq.name: eq for eq in assembly.get_all_parts_in_assembly() if isinstance(eq, ada.Equipment)}


# -- the rules, read off the document ------------------------------------------------------------


def test_a_signal_line_states_its_ends_as_associations_not_connections(loop_doc):
    """``has logical start``/``has logical end``. A SignalConveyingFunction owns no ``<Connection>``
    at all, which is why reading only the piping form left instrumentation entirely unmodelled."""
    assert signal_lines(loop_doc) == {"SIG-1": ("PIF-1_SE001", "PIF-1")}
    assert not [c for c in loop_doc.connections if c.owner_id == "SIG-1"]


def test_the_sensing_half_of_a_loop_stays_a_separate_object(loop_doc):
    """The line runs between a loop function and an element *nested inside it*.

    Folding a nested instrument into its owner -- the rule a ``Chamber`` follows -- puts both ends
    of that line on one object, and the run is then rejected for having no two distinct ends.
    """
    assert [item.id for item in instrument_items(loop_doc)] == ["PIF-1", "PIF-1_SE001"]


def test_a_signal_end_naming_a_function_resolves_to_the_instrument(loop_doc):
    """A placeable element resolves to itself; anything else walks up to its nearest placeable
    owner, so a line naming a function still lands on a real object."""
    assert signal_terminal(loop_doc, "PIF-1_SE001").id == "PIF-1_SE001"
    assert signal_terminal(loop_doc, "PIF-1").id == "PIF-1"


# -- the model that comes out --------------------------------------------------------------------


def test_the_instruments_are_placed_objects(built):
    names = _equipment(built)
    assert "LIC-100" in names, sorted(names)
    assert len([n for n in names if n.startswith("PIF-1_SE001") or n == "LIC-100"]) == 2


def test_an_instrument_carries_the_ifc_class_its_role_implies(built):
    """Not ``IfcBuildingElementProxy``: a controller and a sensor are different things to anyone
    reading the exported IFC, and IFC4 has the classes to say so."""
    classes = {eq.ifc_element_class for eq in _equipment(built).values()}
    assert "IfcController" in classes
    assert "IfcSensor" in classes


def test_instrument_ports_are_signal_not_process(built):
    """Load-bearing rather than cosmetic: ``System.connect`` refuses a category mismatch and the
    compiler then drops the whole system with only a warning."""
    controller = _equipment(built)["LIC-100"]
    assert controller.ports, "the controller has no ports at all"
    assert {port.category for port in controller.ports} == {"signal"}


def test_the_signal_line_becomes_a_routed_system(built):
    """The point of the exercise: the P&ID says these two are joined, and now the 3D model does."""
    cable = [system for system in built.systems if type(system).__name__ == "CableSystem"]
    assert [system.name for system in cable] == ["SIG-1"]


def test_nothing_about_the_instrumentation_was_dropped_quietly(loop_model):
    assert [issue for issue in loop_model.report.issues if "SIG-1" in issue.name] == []


# -- the actuator on the valve it drives ---------------------------------------------------------


def test_an_actuator_is_bound_to_the_valve_it_operates(unit_separator_doc):
    """``is fulfilled by`` is the only statement of which valve an actuating system sits on."""
    actuator = next(item for item in instrument_items(unit_separator_doc) if item.id == "ActuatingSystem-1")
    assert operated_component(unit_separator_doc, actuator).id == "GlobeValve-1"


def test_an_actuator_does_not_borrow_its_valves_tag(unit_separator_assembly):
    """DEXPI numbers the actuating system itself (``PV-202.01``) after the valve it drives.

    Naming the actuator ``PV-202`` too would put two objects with the same tag in the model when the
    valve is materialised as well, and neither name would say which is which.
    """
    names = _equipment(unit_separator_assembly)
    assert "PV-202.01" in names
    assert names["PV-202.01"].ifc_element_class == "IfcActuator"


def test_the_actuator_and_its_valve_are_two_distinct_objects(unit_separator_assembly):
    names = _equipment(unit_separator_assembly)
    assert "PV-202" in names and "PV-202.01" in names
    assert names["PV-202"].ifc_element_class != names["PV-202.01"].ifc_element_class


@pytest.fixture(scope="module")
def unit_separator_doc():
    return read_proteus(ET.parse(_UNIT_SEPARATOR).getroot())


@pytest.fixture(scope="module")
def unit_separator_assembly():
    """The flagship fixture with its in-line valves materialised, so the actuator has a valve to be
    distinguished from."""
    return ada.SystemModel.from_dexpi(_UNIT_SEPARATOR, inline_components="equipment").to_assembly()

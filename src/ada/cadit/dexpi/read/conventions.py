"""The conventions a DEXPI import runs on -- every constant that is a *decision*, in one place.

A P&ID states what the plant is and nothing about how big anything is, which face a signal
connection sits on, what IFC class a materialised tee should carry or where an off-page connector
lands on a deck. Each of those is answered by a convention adapy chose, and a convention is only
worth the name if there is one of it. This module holds them all; the modules that apply them
(:mod:`~ada.cadit.dexpi.nozzle_placers`, :mod:`~ada.cadit.dexpi.equipment_defaults`, the
procedural importer) import from here and hold no numbers of their own.

Logic stays out. The only functions here are the two that translate between the port-direction
spellings, because the vocabulary and its translation are the same decision.
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "DIRECTION_IN",
    "DIRECTION_INOUT",
    "DIRECTION_OUT",
    "ELECTRICAL_FACE",
    "ELECTRICAL_SUPERTYPES",
    "FALLBACK_EQUIPMENT_ENTRY",
    "FLOW_IN",
    "FLOW_OUT",
    "INLINE_BBOX",
    "INLINE_IFC",
    "INSTRUMENT_BBOX",
    "INSTRUMENT_IFC",
    "INSTRUMENT_IFC_DEFAULT",
    "JUNCTION_IFC",
    "NodeFlow",
    "PortDirectionToken",
    "ROOT_EQUIPMENT_CLASS",
    "SIGNAL_FACE",
    "SIGNAL_PORT_KEY",
    "SIGNAL_SUPERTYPES",
    "SITE_ELEVATION",
    "SITE_ELEVATION_FRACTION",
    "SITE_INLET_FACE",
    "SITE_OUTLET_FACE",
    "Z_DRAIN",
    "Z_ELECTRICAL",
    "Z_SHELL",
    "Z_SIGNAL",
    "direction_for",
    "flow_token",
]


# --------------------------------------------------------------------------- #
# Port direction: one vocabulary, spelled two ways
# --------------------------------------------------------------------------- #
#: The direction a port carries, as ``ada.Port`` and every catalog document spell it. The same
#: three strings as ``ada.api.systems.ports.PortDirection``'s values -- compared by value here so
#: this module stays importable without the live-object layer.
PortDirectionToken = Literal["IN", "OUT", "INOUT"]
DIRECTION_IN: PortDirectionToken = "IN"
DIRECTION_OUT: PortDirectionToken = "OUT"
DIRECTION_INOUT: PortDirectionToken = "INOUT"

#: The flow a *node* carries, as the Proteus reader stamps it from ``FlowIn``/``FlowOut`` and as
#: :func:`~ada.cadit.dexpi.equipment_list.connection_flow` derives it from the connectivity graph.
#: Lower-case and two-valued on purpose: a node either has fluid arriving or leaving, and "unknown"
#: is spelled by its absence, never by a third token.
NodeFlow = Literal["in", "out"]
FLOW_IN: NodeFlow = "in"
FLOW_OUT: NodeFlow = "out"

#: Every spelling of "fluid arrives here" / "fluid leaves here" a document or a caller may use:
#: the node flow, the Proteus attribute names, the plain English of a hand-written definition
#: list, and the source/target vocabulary of an off-page connector.
_INBOUND_SPELLINGS = ("in", "flowin", "inlet", "source")
_OUTBOUND_SPELLINGS = ("out", "flowout", "outlet", "target")


def direction_for(flow: str | None) -> PortDirectionToken:
    """``IN``/``OUT`` from a node's flow, ``INOUT`` when the document does not say.

    The Proteus reader stamps ``"in"``/``"out"``; DEXPI 2.0 and the off-page-connector classes
    spell it out. An unset flow is genuinely unknown, not a default of ``IN`` -- guessing here
    would put an outlet on the wrong face of every vessel in the file.
    """
    if not flow:
        return DIRECTION_INOUT
    token = flow.strip().lower()
    if token in _INBOUND_SPELLINGS:
        return DIRECTION_IN
    if token in _OUTBOUND_SPELLINGS:
        return DIRECTION_OUT
    return DIRECTION_INOUT


def flow_token(direction: object) -> NodeFlow | None:
    """The node flow to write for a port direction -- the inverse of :func:`direction_for`.

    Accepts the ``PortDirection`` enum, its value, or None; ``INOUT`` and anything unrecognised
    become None, because a Proteus node with neither ``FlowIn`` nor ``FlowOut`` is exactly how an
    undirected port is stated.
    """
    value = getattr(direction, "value", direction)
    if value == DIRECTION_IN:
        return FLOW_IN
    if value == DIRECTION_OUT:
        return FLOW_OUT
    return None


# --------------------------------------------------------------------------- #
# Nozzle placement on an equipment box
# --------------------------------------------------------------------------- #
#: Where a strategy puts a service connection. Mirrors the archetypes in
#: ``ada.topo_model.equipment`` (power on +X, control signal on +Y), so a DEXPI-imported pump and a
#: hand-built one wire up the same way.
SIGNAL_FACE = "+Y"
ELECTRICAL_FACE = "+X"

#: Height fractions of the envelope reserved per service, so a signal connection can never land on
#: top of a process nozzle sharing the same face.
Z_ELECTRICAL = 0.30
Z_SIGNAL = 0.70
Z_SHELL = 0.50
Z_DRAIN = 0.15

#: Supertypes that make a connection a signal or an electrical supply. Checked only AFTER the
#: ``Nozzle`` branch -- see :func:`~ada.cadit.dexpi.nozzle_placers.category_for`.
SIGNAL_SUPERTYPES = (
    "InstrumentNozzle",
    "ProcessInstrumentationFunction",
    "SignalConveyingFunction",
    "SignalOffPageConnector",
)
ELECTRICAL_SUPERTYPES = (
    "ActuatingElectricalFunction",
    "ActuatingElectricalLocation",
    "ActuatingElectricalSystem",
)


# --------------------------------------------------------------------------- #
# Class defaults
# --------------------------------------------------------------------------- #
#: The catch-all entry name in the curated defaults table. Every ``Plant/ProcessEquipment`` class
#: reaches it if nothing more specific matches, because it is their common abstract base -- note
#: that this is ``ProcessEquipment`` and not ``Equipment``: DEXPI has no class by the latter name
#: (it is only a Proteus element tag), and ``Chamber``/``Nozzle`` deliberately do not derive from
#: it.
ROOT_EQUIPMENT_CLASS = "ProcessEquipment"

#: Used only if the defaults table is somehow missing its root entry, so a lookup can never return
#: None and leave a caller to invent a size of its own.
FALLBACK_EQUIPMENT_ENTRY: dict = {
    "bbox": [2.0, 2.0, 2.0],
    "ifc": "IfcBuildingElementProxy",
    "nozzles": "generic",
    "density": 200.0,
}


# --------------------------------------------------------------------------- #
# Equipment the importer synthesises
# --------------------------------------------------------------------------- #
#: Envelope and IFC class for an in-line component materialised as its own equipment under
#: ``inline_components="equipment"``. Small and square on purpose: a valve body is a detail, and
#: giving it a considered size would be inventing data the P&ID does not hold.
INLINE_BBOX = (0.4, 0.4, 0.4)
INLINE_IFC = "IfcValve"

#: IFC class for a branch point materialised as its own equipment. Same envelope as an in-line
#: component -- a tee body is a detail too -- but a fitting rather than a valve.
JUNCTION_IFC = "IfcPipeFitting"

#: Envelope for an instrument materialised as its own equipment. Smaller than an in-line component
#: on purpose: a transmitter or a controller is a box on a stand, not a body the process runs
#: through, and the P&ID says nothing about its size either way.
INSTRUMENT_BBOX = (0.3, 0.3, 0.3)

#: DEXPI instrumentation class -> IFC4 distribution *control* element. These are deliberately not
#: ``IfcBuildingElementProxy``: an actuator and a controller are different things to anyone reading
#: the exported IFC, and IFC4 has the classes to say so.
INSTRUMENT_IFC: dict[str, str] = {
    "ActuatingSystem": "IfcActuator",
    "ActuatingElectricalSystem": "IfcActuator",
    "ControlledActuator": "IfcActuator",
    # The function an actuating system performs. It is the acting half of a loop and belongs with
    # the actuators, not in the IfcFlowInstrument catch-all it would otherwise fall to -- the
    # supertype DAG does not connect it to ControlledActuator.
    "ActuatingFunction": "IfcActuator",
    "Positioner": "IfcActuator",
    "ProcessSignalGeneratingSystem": "IfcSensor",
    "ProcessSignalGeneratingFunction": "IfcSensor",
    "ProcessInstrumentationFunction": "IfcController",
}
INSTRUMENT_IFC_DEFAULT = "IfcFlowInstrument"

#: Suffix for the synthetic signal port given to an instrument that declares no connection node of
#: its own -- which is most of them: a DEXPI instrument states its connectivity with associations,
#: not with ``<ConnectionPoints>``, so there is no node to derive a port from.
SIGNAL_PORT_KEY = "#signal"


# --------------------------------------------------------------------------- #
# Site terminals on a deck boundary
# --------------------------------------------------------------------------- #
#: Height of a site terminal above the floor of the deck it is placed on, and the fraction of the
#: deck height to fall back to on a deck shallower than that.
SITE_ELEVATION = 1.5
SITE_ELEVATION_FRACTION = 0.4

#: The deck face an off-page connector enters and leaves through: inputs come in through ``-X``
#: and outputs go out through ``+X``, so a run's two boundary crossings never share a face.
SITE_INLET_FACE = "-X"
SITE_OUTLET_FACE = "+X"

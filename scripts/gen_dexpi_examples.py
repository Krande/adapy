"""Regenerate the checked-in DEXPI example files in ``files/dexpi_files/``.

Each example is built **once**, in Python, as a neutral
:class:`~ada.cadit.dexpi.model.DexpiDocument`, and then written through both writers. That is what
makes the ``_proteus``/``_dexpi20`` pair of each example a cross-flavour equality proof by
construction rather than by assertion: the two files cannot disagree about the plant, because there
is only one statement of it and two serializations of that statement.

Two examples:

``tiny_two_equipment``
    A tank, a pump, and one piping segment through a ball valve. Deliberately opens **every**
    ``<ConnectionPoints>`` with a ``-DefaultNode`` anchor, so the smallest fixture in the repository
    exercises the 0-based positional-index trap that the Proteus writer has to get right.

``unit_separator``
    A three-phase separator package: a separator with a boot chamber and five nozzles, spared pumps
    giving a real branch, a heat exchanger, in-line valves and fittings across six lines, two
    off-page connectors, a property break, an instrumentation function with its actuating system, a
    plant-structure item, a custom attribute set, an association, and shape-catalogue and drawing
    stubs that only Proteus can carry.

The outputs are committed, and ``tests/core/cadit/dexpi/test_fixtures_current.py`` fails if they
drift from what this script produces. Regenerate with::

    python scripts/gen_dexpi_examples.py

Nothing here reaches the network, and nothing it writes names a machine, a user or an
organisation -- the documents say they were originated by ``adapy`` and nothing else.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import xml.etree.ElementTree as ET

_REPO = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:  # so the script runs from a plain checkout
    sys.path.insert(0, str(_REPO / "src"))

from ada.cadit.dexpi.model import (  # noqa: E402
    DexpiAssociation,
    DexpiAttribute,
    DexpiConnection,
    DexpiDocument,
    DexpiFlavour,
    DexpiHeader,
    DexpiItem,
    DexpiNode,
    DexpiPlacement,
    classify,
)
from ada.cadit.dexpi.write import to_element  # noqa: E402
from ada.cadit.dexpi.write import xml_utils  # noqa: E402

OUT_DIR = _REPO / "files" / "dexpi_files"

# What the fixtures say about their own provenance. A neutral library name, never a machine or a
# vendor, and no export timestamp -- a date would make every regeneration a diff.
ORIGINATING_SYSTEM = "adapy"
SCHEMA_VERSION = "4.1.1"

# The DEXPI attribute set, and a second one exercising the vendor-set path that a Proteus document
# can express and DEXPI 2.0 cannot.
DEXPI_SET = "DexpiAttributes"
CUSTOM_SET = "DexpiCustomAttributes"

FLAVOURS = {"proteus": DexpiFlavour.PROTEUS, "dexpi20": DexpiFlavour.DEXPI20}


# -- authoring helpers --------------------------------------------------------------------------------


def attribute(name: str, value, *, fmt: str = "string", units: str | None = None, set_name: str = DEXPI_SET):
    """One ``GenericAttribute``, spelled the way a Proteus emitter spells it."""
    return DexpiAttribute(name=name, value=str(value), format=fmt, units=units, set_name=set_name)


def tag_attribute(tag: str, *, sub: bool = False) -> DexpiAttribute:
    return attribute("SubTagNameAssignmentClass" if sub else "TagNameAssignmentClass", tag)


def diameter_attributes(dn: int) -> list[DexpiAttribute]:
    return [
        attribute("NominalDiameterNumericalValueRepresentationAssignmentClass", dn),
        attribute("NominalDiameterTypeRepresentationAssignmentClass", "DN"),
    ]


def connection_points(owner_id: str, flows: list[str | None], *, dn: int | None = None) -> list[DexpiNode]:
    """The owner's nodes: **the symbol anchor at ordinal 1**, then one node per entry in ``flows``.

    The anchor is not decoration. Proteus addresses connection points by 0-based position and the
    anchor holds slot 0, so a fixture without one would let a writer that emitted 1-based indices
    pass every test in the suite.
    """
    nodes = [DexpiNode(id=f"{owner_id}-DefaultNode", ordinal=1, owner_id=owner_id, is_anchor=True)]
    for index, flow in enumerate(flows):
        nodes.append(
            DexpiNode(
                id=f"{owner_id}-Node-{index + 1}",
                ordinal=index + 2,
                owner_id=owner_id,
                node_type="process",
                flow=flow,
                nominal_diameter=None if dn is None else dn / 1000.0,
            )
        )
    return nodes


def placement(x: float, y: float, *, width: float = 20.0, height: float = 20.0) -> DexpiPlacement:
    """A symbol on the sheet, in the drawing's own millimetres. Never plant geometry."""
    return DexpiPlacement(
        location=(x, y, 0.0),
        axis=(0.0, 0.0, 1.0),
        reference=(1.0, 0.0, 0.0),
        extent_min=(x - width / 2, y - height / 2, 0.0),
        extent_max=(x + width / 2, y + height / 2, 0.0),
    )


class Example:
    """A document under construction, with the bookkeeping the neutral model expects."""

    def __init__(self, project: str):
        self.doc = DexpiDocument(
            flavour=DexpiFlavour.PROTEUS,
            header=DexpiHeader(
                originating_system=ORIGINATING_SYSTEM,
                originating_system_vendor=ORIGINATING_SYSTEM,
                schema_version=SCHEMA_VERSION,
                units="mm",
                project=project,
            ),
        )

    def add(
        self,
        item_id: str,
        class_name: str,
        *,
        role: str,
        parent: str | None = None,
        attributes: list[DexpiAttribute] | None = None,
        nodes: list[DexpiNode] | None = None,
        place: DexpiPlacement | None = None,
        associations: list[DexpiAssociation] | None = None,
    ) -> DexpiItem:
        """Register one item.

        ``role`` is the Proteus element tag, which is what the reader records on
        ``composition_role``; the DEXPI 2.0 writer derives its own composition property from the
        item's kind instead, because a tag is not a property name.
        """
        item = DexpiItem(
            id=item_id,
            class_name=class_name,
            kind=classify(class_name),
            composition_role=role,
            nodes=nodes or [],
            attributes=attributes or [],
            associations=associations or [],
            placement=place,
        )
        return self.doc.add(item, parent)

    def connect(self, owner: str, from_item: str, from_ordinal: int, to_item: str, to_ordinal: int) -> None:
        """One edge, addressed by **node ordinal** -- ordinal 1 is the anchor, 2 the first process
        node. The writers turn that into whatever their flavour addresses nodes with."""
        self.doc.connections.append(
            DexpiConnection(
                from_item=from_item,
                from_node=self.doc.items[from_item].node_by_ordinal(from_ordinal).id,
                to_item=to_item,
                to_node=self.doc.items[to_item].node_by_ordinal(to_ordinal).id,
                owner_id=owner,
            )
        )

    def extra(self, element: ET.Element) -> None:
        """A Proteus subtree the model has no concept of. Echoed verbatim on a Proteus write and
        dropped on a DEXPI 2.0 one, which has no vocabulary for it."""
        self.doc.extras.append(element)


# -- tiny_two_equipment -------------------------------------------------------------------------------


def build_tiny() -> DexpiDocument:
    """A tank, a pump and one segment through a ball valve."""
    example = Example("Tiny two equipment example")

    example.add(
        "Tank-1",
        "Tank",
        role="Equipment",
        attributes=[
            tag_attribute("T-100"),
            attribute("EquipmentDescriptionAssignmentClass", "Feed tank"),
            attribute("NominalCapacity(Volume)", 26, fmt="double", units="MetreCubed"),
        ],
        place=placement(120.0, 300.0, width=60.0, height=80.0),
    )
    example.add(
        "Nozzle-1",
        "Nozzle",
        role="Nozzle",
        parent="Tank-1",
        attributes=[tag_attribute("N1", sub=True)],
        nodes=connection_points("Nozzle-1", ["out"], dn=80),
    )
    example.add(
        "Nozzle-2",
        "Nozzle",
        role="Nozzle",
        parent="Tank-1",
        attributes=[tag_attribute("N2", sub=True)],
        nodes=connection_points("Nozzle-2", ["in"], dn=50),
    )

    example.add(
        "CentrifugalPump-1",
        "CentrifugalPump",
        role="Equipment",
        attributes=[tag_attribute("P-100"), attribute("EquipmentDescriptionAssignmentClass", "Feed pump")],
        place=placement(320.0, 300.0, width=40.0, height=40.0),
    )
    example.add(
        "Nozzle-3",
        "Nozzle",
        role="Nozzle",
        parent="CentrifugalPump-1",
        attributes=[tag_attribute("S", sub=True)],
        nodes=connection_points("Nozzle-3", ["in"], dn=80),
    )
    example.add(
        "Nozzle-4",
        "Nozzle",
        role="Nozzle",
        parent="CentrifugalPump-1",
        attributes=[tag_attribute("D", sub=True)],
        nodes=connection_points("Nozzle-4", ["out"], dn=50),
    )

    example.add(
        "PipingNetworkSystem-1",
        "PipingNetworkSystem",
        role="PipingNetworkSystem",
        attributes=[
            attribute("LineNumberAssignmentClass", "100"),
            attribute("FluidCodeAssignmentClass", "PW"),
            attribute("PipingClassCodeAssignmentClass", "75HB"),
            *diameter_attributes(80),
        ],
    )
    example.add(
        "PipingNetworkSegment-1",
        "PipingNetworkSegment",
        role="PipingNetworkSegment",
        parent="PipingNetworkSystem-1",
        attributes=[attribute("SegmentNumberAssignmentClass", "1")],
    )
    example.add(
        "BallValve-1",
        "BallValve",
        role="PipingComponent",
        parent="PipingNetworkSegment-1",
        attributes=[tag_attribute("HV-100"), *diameter_attributes(80)],
        nodes=connection_points("BallValve-1", ["in", "out"], dn=80),
        place=placement(220.0, 300.0, width=16.0, height=16.0),
    )

    example.connect("PipingNetworkSegment-1", "Nozzle-1", 2, "BallValve-1", 2)
    example.connect("PipingNetworkSegment-1", "BallValve-1", 3, "Nozzle-3", 2)

    return example.doc


# -- unit_separator -----------------------------------------------------------------------------------


def _valve(example: Example, item_id: str, class_name: str, tag: str, segment: str, dn: int, x: float) -> None:
    example.add(
        item_id,
        class_name,
        role="PipingComponent",
        parent=segment,
        attributes=[tag_attribute(tag), *diameter_attributes(dn)],
        nodes=connection_points(item_id, ["in", "out"], dn=dn),
        place=placement(x, 300.0, width=16.0, height=16.0),
    )


def _line(example: Example, item_id: str, number: str, fluid: str, dn: int) -> None:
    example.add(
        item_id,
        "PipingNetworkSystem",
        role="PipingNetworkSystem",
        attributes=[
            attribute("LineNumberAssignmentClass", number),
            attribute("FluidCodeAssignmentClass", fluid),
            attribute("PipingClassCodeAssignmentClass", "75HB"),
            *diameter_attributes(dn),
        ],
    )


def _segment(example: Example, item_id: str, line: str, number: str) -> None:
    example.add(
        item_id,
        "PipingNetworkSegment",
        role="PipingNetworkSegment",
        parent=line,
        attributes=[attribute("SegmentNumberAssignmentClass", number)],
    )


def _shape_catalogue() -> ET.Element:
    """A Proteus symbol library stub. The reader never descends into one and echoes it whole."""
    catalogue = xml_utils.element("ShapeCatalogue", {"Name": "AdapyExampleSymbols"})
    symbol = xml_utils.sub_element(
        catalogue, "Equipment", {"ID": "Symbol-Separator", "ComponentClass": "Separator"}
    )
    extent = xml_utils.sub_element(symbol, "Extent")
    xml_utils.point_element(extent, "Min", (0.0, 0.0, 0.0))
    xml_utils.point_element(extent, "Max", (100.0, 40.0, 0.0))
    return catalogue


def _drawing() -> ET.Element:
    """A Proteus sheet-layout stub, echoed whole for the same reason."""
    drawing = xml_utils.element("Drawing", {"Name": "Separator package", "Type": "PID"})
    extent = xml_utils.sub_element(drawing, "Extent")
    xml_utils.point_element(extent, "Min", (0.0, 0.0, 0.0))
    xml_utils.point_element(extent, "Max", (841.0, 594.0, 0.0))
    return drawing


def build_unit_separator() -> DexpiDocument:
    """A three-phase separator package: the realistic example."""
    example = Example("Separator package example")

    # -- plant structure ---------------------------------------------------------------------------
    example.add(
        "PlantSection-1",
        "PlantSection",
        role="PlantStructureItem",
        attributes=[tag_attribute("U-200"), attribute("EquipmentDescriptionAssignmentClass", "Separation unit")],
    )

    # -- equipment ---------------------------------------------------------------------------------
    example.add(
        "Separator-1",
        "Separator",
        role="Equipment",
        attributes=[
            tag_attribute("V-201"),
            attribute("EquipmentDescriptionAssignmentClass", "Three phase separator"),
            attribute("UpperLimitDesignPressure", 10, fmt="double", units="Bar"),
            attribute("UpperLimitDesignTemperature", 120, fmt="double", units="DegreeCelsius"),
            # A vendor set beside the DEXPI one: something Proteus can say and DEXPI 2.0 cannot.
            attribute("ProjectPhase", "FEED", set_name=CUSTOM_SET),
            attribute("CriticalityRating", "A", set_name=CUSTOM_SET),
        ],
        place=placement(200.0, 400.0, width=160.0, height=70.0),
    )
    for nozzle_id, tag, flow, dn in (
        ("Nozzle-1", "N1", "in", 150),
        ("Nozzle-2", "N2", "out", 200),
        ("Nozzle-3", "N3", "out", 100),
        ("Nozzle-4", "N4", "out", 80),
    ):
        example.add(
            nozzle_id,
            "Nozzle",
            role="Nozzle",
            parent="Separator-1",
            attributes=[tag_attribute(tag, sub=True), *diameter_attributes(dn)],
            nodes=connection_points(nozzle_id, [flow], dn=dn),
        )

    example.add(
        "Chamber-1",
        "Chamber",
        role="Equipment",
        parent="Separator-1",
        attributes=[
            tag_attribute("Boot", sub=True),
            attribute("NominalDiameter", 0.6, fmt="double", units="Metre"),
        ],
    )
    example.add(
        "Nozzle-5",
        "Nozzle",
        role="Nozzle",
        parent="Chamber-1",
        attributes=[tag_attribute("N5", sub=True), *diameter_attributes(50)],
        nodes=connection_points("Nozzle-5", ["out"], dn=50),
    )

    for pump_id, tag, x in (("CentrifugalPump-1", "P-201A", 420.0), ("CentrifugalPump-2", "P-201B", 420.0)):
        example.add(
            pump_id,
            "CentrifugalPump",
            role="Equipment",
            attributes=[tag_attribute(tag), attribute("EquipmentDescriptionAssignmentClass", "Condensate pump")],
            place=placement(x, 200.0 if tag.endswith("A") else 140.0, width=40.0, height=40.0),
        )
    for nozzle_id, pump_id, tag, flow, dn in (
        ("Nozzle-6", "CentrifugalPump-1", "S", "in", 100),
        ("Nozzle-7", "CentrifugalPump-1", "D", "out", 80),
        ("Nozzle-8", "CentrifugalPump-2", "S", "in", 100),
        ("Nozzle-9", "CentrifugalPump-2", "D", "out", 80),
    ):
        example.add(
            nozzle_id,
            "Nozzle",
            role="Nozzle",
            parent=pump_id,
            attributes=[tag_attribute(tag, sub=True), *diameter_attributes(dn)],
            nodes=connection_points(nozzle_id, [flow], dn=dn),
        )

    example.add(
        "TubularHeatExchanger-1",
        "TubularHeatExchanger",
        role="Equipment",
        attributes=[tag_attribute("E-201"), attribute("EquipmentDescriptionAssignmentClass", "Condensate cooler")],
        place=placement(620.0, 170.0, width=90.0, height=40.0),
    )
    for nozzle_id, tag, flow, dn in (("Nozzle-10", "T1", "in", 80), ("Nozzle-11", "T2", "in", 50)):
        example.add(
            nozzle_id,
            "Nozzle",
            role="Nozzle",
            parent="TubularHeatExchanger-1",
            attributes=[tag_attribute(tag, sub=True), *diameter_attributes(dn)],
            nodes=connection_points(nozzle_id, [flow], dn=dn),
        )

    # -- line 201: feed, from off the drawing ------------------------------------------------------
    _line(example, "PipingNetworkSystem-1", "201", "HC", 150)
    _segment(example, "PipingNetworkSegment-1", "PipingNetworkSystem-1", "1")
    example.add(
        "PipeOffPageConnector-1",
        "FlowInPipeOffPageConnector",
        role="PipeOffPageConnector",
        parent="PipingNetworkSegment-1",
        attributes=[tag_attribute("OPC-201")],
        nodes=connection_points("PipeOffPageConnector-1", ["out"], dn=150),
        place=placement(20.0, 400.0, width=20.0, height=12.0),
    )
    _valve(example, "Strainer-1", "Strainer", "ST-201", "PipingNetworkSegment-1", 150, 60.0)
    _valve(example, "BallValve-1", "BallValve", "HV-201", "PipingNetworkSegment-1", 150, 110.0)
    example.connect("PipingNetworkSegment-1", "PipeOffPageConnector-1", 2, "Strainer-1", 2)
    example.connect("PipingNetworkSegment-1", "Strainer-1", 3, "BallValve-1", 2)
    example.connect("PipingNetworkSegment-1", "BallValve-1", 3, "Nozzle-1", 2)

    # -- line 202: gas out, through the control valve ----------------------------------------------
    _line(example, "PipingNetworkSystem-2", "202", "GA", 200)
    _segment(example, "PipingNetworkSegment-2", "PipingNetworkSystem-2", "1")
    _valve(example, "GlobeValve-1", "GlobeValve", "PV-202", "PipingNetworkSegment-2", 200, 300.0)
    _valve(
        example,
        "RestrictionOrifice-1",
        "RestrictionOrifice",
        "RO-202",
        "PipingNetworkSegment-2",
        200,
        350.0,
    )
    example.add(
        "PipeOffPageConnector-2",
        "FlowOutPipeOffPageConnector",
        role="PipeOffPageConnector",
        parent="PipingNetworkSegment-2",
        attributes=[tag_attribute("OPC-202")],
        nodes=connection_points("PipeOffPageConnector-2", ["in"], dn=200),
        place=placement(760.0, 460.0, width=20.0, height=12.0),
    )
    example.connect("PipingNetworkSegment-2", "Nozzle-2", 2, "GlobeValve-1", 2)
    example.connect("PipingNetworkSegment-2", "GlobeValve-1", 3, "RestrictionOrifice-1", 2)
    example.connect("PipingNetworkSegment-2", "RestrictionOrifice-1", 3, "PipeOffPageConnector-2", 2)

    # -- line 203: liquid out, branching to the spared pumps ---------------------------------------
    _line(example, "PipingNetworkSystem-3", "203", "HC", 100)
    _segment(example, "PipingNetworkSegment-3", "PipingNetworkSystem-3", "1")
    example.add(
        "PipeTee-1",
        "PipeTee",
        role="PipingComponent",
        parent="PipingNetworkSegment-3",
        attributes=[tag_attribute("TE-203"), *diameter_attributes(100)],
        # Proteus has one @FlowIn and one @FlowOut per item, so a tee can only state two of its
        # three directions; the third is left for a consumer to derive from the connections.
        nodes=connection_points("PipeTee-1", ["in", "out", None], dn=100),
        place=placement(340.0, 240.0, width=14.0, height=14.0),
    )
    example.connect("PipingNetworkSegment-3", "Nozzle-3", 2, "PipeTee-1", 2)

    _segment(example, "PipingNetworkSegment-4", "PipingNetworkSystem-3", "2")
    _valve(example, "GateValve-1", "GateValve", "HV-203A", "PipingNetworkSegment-4", 100, 380.0)
    example.connect("PipingNetworkSegment-4", "PipeTee-1", 3, "GateValve-1", 2)
    example.connect("PipingNetworkSegment-4", "GateValve-1", 3, "Nozzle-6", 2)

    _segment(example, "PipingNetworkSegment-5", "PipingNetworkSystem-3", "3")
    _valve(example, "GateValve-2", "GateValve", "HV-203B", "PipingNetworkSegment-5", 100, 380.0)
    example.connect("PipingNetworkSegment-5", "PipeTee-1", 4, "GateValve-2", 2)
    example.connect("PipingNetworkSegment-5", "GateValve-2", 3, "Nozzle-8", 2)

    # -- line 204: pump discharge, joining again and going to the cooler ---------------------------
    _line(example, "PipingNetworkSystem-4", "204", "HC", 80)
    _segment(example, "PipingNetworkSegment-6", "PipingNetworkSystem-4", "1")
    example.add(
        "PipeTee-2",
        "PipeTee",
        role="PipingComponent",
        parent="PipingNetworkSegment-6",
        attributes=[tag_attribute("TE-204"), *diameter_attributes(80)],
        nodes=connection_points("PipeTee-2", ["in", None, "out"], dn=80),
        place=placement(520.0, 170.0, width=14.0, height=14.0),
    )
    _valve(
        example,
        "SwingCheckValve-1",
        "SwingCheckValve",
        "NRV-204A",
        "PipingNetworkSegment-6",
        80,
        470.0,
    )
    example.connect("PipingNetworkSegment-6", "Nozzle-7", 2, "SwingCheckValve-1", 2)
    example.connect("PipingNetworkSegment-6", "SwingCheckValve-1", 3, "PipeTee-2", 2)

    _segment(example, "PipingNetworkSegment-7", "PipingNetworkSystem-4", "2")
    _valve(
        example,
        "SwingCheckValve-2",
        "SwingCheckValve",
        "NRV-204B",
        "PipingNetworkSegment-7",
        80,
        470.0,
    )
    example.connect("PipingNetworkSegment-7", "Nozzle-9", 2, "SwingCheckValve-2", 2)
    example.connect("PipingNetworkSegment-7", "SwingCheckValve-2", 3, "PipeTee-2", 3)

    _segment(example, "PipingNetworkSegment-8", "PipingNetworkSystem-4", "3")
    example.add(
        "PipeReducer-1",
        "PipeReducer",
        role="PipingComponent",
        parent="PipingNetworkSegment-8",
        attributes=[tag_attribute("RE-204"), *diameter_attributes(80)],
        nodes=connection_points("PipeReducer-1", ["in", "out"], dn=80),
        place=placement(560.0, 170.0, width=14.0, height=14.0),
    )
    example.add(
        "PropertyBreak-1",
        "PropertyBreak",
        role="PropertyBreak",
        parent="PipingNetworkSegment-8",
        attributes=[attribute("PipingClassCodeAssignmentClass", "150HB")],
        place=placement(590.0, 170.0, width=8.0, height=16.0),
    )
    # A drawn centre line: the model keeps the coordinates, the writer puts them back as a PolyLine.
    example.doc.items["PipingNetworkSegment-8"].placement = DexpiPlacement(
        polyline=[(520.0, 170.0, 0.0), (560.0, 170.0, 0.0), (620.0, 170.0, 0.0)]
    )
    example.connect("PipingNetworkSegment-8", "PipeTee-2", 4, "PipeReducer-1", 2)
    example.connect("PipingNetworkSegment-8", "PipeReducer-1", 3, "Nozzle-10", 2)

    # -- line 205: relief, ending at the safety valve ----------------------------------------------
    _line(example, "PipingNetworkSystem-5", "205", "HC", 80)
    _segment(example, "PipingNetworkSegment-9", "PipingNetworkSystem-5", "1")
    _valve(
        example,
        "SafetyValveOrFitting-1",
        "SafetyValveOrFitting",
        "PSV-205",
        "PipingNetworkSegment-9",
        80,
        240.0,
    )
    example.connect("PipingNetworkSegment-9", "Nozzle-4", 2, "SafetyValveOrFitting-1", 2)

    # -- line 206: boot drain ----------------------------------------------------------------------
    _line(example, "PipingNetworkSystem-6", "206", "HC", 50)
    _segment(example, "PipingNetworkSegment-10", "PipingNetworkSystem-6", "1")
    _valve(example, "GlobeValve-2", "GlobeValve", "HV-206", "PipingNetworkSegment-10", 50, 300.0)
    example.connect("PipingNetworkSegment-10", "Nozzle-5", 2, "GlobeValve-2", 2)
    example.connect("PipingNetworkSegment-10", "GlobeValve-2", 3, "Nozzle-11", 2)

    # -- instrumentation ---------------------------------------------------------------------------
    example.add(
        "ProcessInstrumentationFunction-1",
        "ProcessInstrumentationFunction",
        role="ProcessInstrumentationFunction",
        attributes=[tag_attribute("LIC-201")],
        place=placement(300.0, 480.0, width=24.0, height=24.0),
    )
    example.add(
        "ActuatingSystem-1",
        "ActuatingSystem",
        role="ActuatingSystem",
        attributes=[attribute("ActuatingSystemNumber", "PV-202.01")],
        associations=[
            DexpiAssociation(type="is fulfilled by", owner_id="ActuatingSystem-1", target_ids=["GlobeValve-1"])
        ],
    )

    # -- presentation, which only Proteus can carry ------------------------------------------------
    example.extra(_shape_catalogue())
    example.extra(_drawing())

    return example.doc


# -- generation ---------------------------------------------------------------------------------------


def documents() -> dict[str, DexpiDocument]:
    """The examples, by base file name. One document each, whatever flavour it is written in."""
    return {"tiny_two_equipment": build_tiny(), "unit_separator": build_unit_separator()}


def rendered() -> dict[str, str]:
    """``{file name: file text}`` for every example in every flavour.

    This, not the files on disk, is what the drift test compares against.
    """
    out: dict[str, str] = {}
    for name, doc in documents().items():
        for suffix, flavour in FLAVOURS.items():
            out[f"{name}_{suffix}.xml"] = xml_utils.to_text(to_element(doc, flavour))
    return out


def generate(destination: pathlib.Path = OUT_DIR) -> list[pathlib.Path]:
    """Write every example into ``destination`` and return the paths written."""
    destination.mkdir(parents=True, exist_ok=True)

    written: list[pathlib.Path] = []
    for name, text in rendered().items():
        path = destination / name
        with path.open("w", encoding="utf-8", newline="\n") as fp:
            fp.write(text)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR, help="where to write the examples")
    args = parser.parse_args(argv)

    for path in generate(args.out_dir):
        print(f"wrote {path.relative_to(_REPO) if path.is_relative_to(_REPO) else path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

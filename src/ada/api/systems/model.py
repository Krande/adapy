"""The adapy-native model of a plant's systems, between the wire format and the 3D model.

Three layers, each with one job:

``DexpiDocument``
    A faithful parse of the file. Every item, every attribute, and the raw ``ET.Element`` of
    anything adapy does not model, so a re-write drops nothing.

:class:`SystemModel` (here)
    The native representation: :class:`~ada.api.spatial.equipment.Equipment` with real
    :class:`~ada.api.systems.ports.Port`\\ s, and the :class:`~ada.api.systems.base.System`\\ s
    joining them. **No coordinates.** A P&ID states what exists and what is connected to what; it
    says nothing whatsoever about where any of it stands.

:class:`~ada.Assembly`
    The 3D model: generated decks, placed equipment, routed runs, structure.

The split exists because reading a P&ID and building a plant are different jobs with different
arguments, and fusing them grew ``ada.from_dexpi`` to thirteen parameters of which roughly half
described the *build*. It also settles where the export belongs: **DEXPI has no coordinates**, so
nothing the 3D build produces -- deck elevations, placements, routed geometry -- is expressible in
it. The write-back can only be meaningful from this layer, which is why :meth:`SystemModel.to_dexpi`
lives here and the built assembly is a pure derivative.

The name is ``SystemModel`` rather than ``ProcessModel`` on purpose. adapy models
:class:`~ada.api.systems.base.DuctSystem`, :class:`~ada.api.systems.base.CableSystem` and
:class:`~ada.api.systems.base.ElectricalSystem` as peers of
:class:`~ada.api.systems.base.PipingSystem`, and the take-off treats HVAC and electrical as
disciplines in their own right -- a pure cabling model is a first-class citizen here, and "process"
would claim otherwise. Nothing in this class is DEXPI-specific either; DEXPI is simply its first
reader.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable

if TYPE_CHECKING:
    from ada import Assembly, Equipment
    from ada.api.systems.base import System
    from ada.cadit.dexpi.model import DexpiDocument
    from ada.topo_model.build_spec import ProceduralBuildSpec

__all__ = ["SystemModel"]


@dataclass
class SystemModel:
    """Equipment, ports and the systems joining them -- what a P&ID actually says.

    Built by a reader (today :func:`ada.from_dexpi`), consumed by :meth:`to_assembly` to produce a
    3D model and by :meth:`to_dexpi` to write one back out. Both directions start here; neither
    needs the other to have run.
    """

    #: Human-readable name, used for the built assembly and as the exported document's project.
    name: str = "SystemModel"

    #: The equipment this model describes, with their ports. Unplaced: every one sits at the origin
    #: because the source says nothing about placement, and inventing a coordinate here would be a
    #: claim the P&ID does not support.
    equipment: list["Equipment"] = field(default_factory=list)

    #: The systems joining that equipment's ports -- piping, duct, cable, electrical.
    systems: list["System"] = field(default_factory=list)

    #: ``{slug: equipment document}``. A dict's ``.get`` is a valid ``equipment_resolver``, so this
    #: feeds the procedural compiler directly.
    catalog: dict = field(default_factory=dict, repr=False)

    #: What the read could not carry through, never silently dropped. Read-stage gaps only -- the
    #: build reports its own on the assembly it produces.
    report: Any = None

    #: Provenance and anything the reader wants to carry (source path, wire flavour, warnings).
    metadata: dict = field(default_factory=dict, repr=False)

    #: The document this model was read from, kept so :meth:`to_dexpi` can merge into it rather than
    #: regenerate. ``None`` for a model that was not read from a DEXPI file.
    source_document: "DexpiDocument | None" = field(default=None, repr=False)

    #: How to turn this model into the procedural compiler's input, given the build's own rules.
    #: ``spec -> (procedural document, equipment catalog)``.
    #:
    #: A callable rather than a stored document, and that is the point of the whole split: the
    #: generated decks and the equipment coordinates are *products of the build*, not properties of
    #: the P&ID, so they cannot be computed until the build's :class:`LayoutRules` are known. Storing
    #: a finished procedural document here would bake one layout into the model and make a second
    #: build with different deck bounds impossible.
    #:
    #: Keeping it a callable also keeps this class free of any wire format: the reader supplies it,
    #: and nothing here knows or cares that DEXPI produced this model.
    procedural_factory: "Callable[[ProceduralBuildSpec], tuple[dict, dict]] | None" = field(default=None, repr=False)

    # -- introspection ---------------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"SystemModel({self.name!r}, {len(self.equipment)} equipment, " f"{len(self.systems)} system(s))"

    @property
    def equipment_by_name(self) -> dict[str, "Equipment"]:
        return {eq.name: eq for eq in self.equipment}

    def ports(self) -> Iterable:
        """Every port on every piece of equipment, in equipment order."""
        for eq in self.equipment:
            yield from (eq.ports or [])

    # -- readers ---------------------------------------------------------------------------------

    @classmethod
    def from_dexpi(
        cls,
        path,
        *,
        name: str | None = None,
        flavour: str | None = None,
        definitions=None,
        inline_components: str = "metadata",
        strict: bool = False,
    ) -> "SystemModel":
        """Read a DEXPI P&ID -- either flavour, sniffed from the root tag -- into a system model.

        This reads and resolves; it does not build. Every item resolves through the equipment
        definition list to a physical envelope with real ports, and every ``PipingNetworkSegment``
        and signal line becomes a system joining them. **No coordinates**: a P&ID says what exists
        and what is connected to what, and nothing about where any of it stands.

        ``definitions`` is the equipment definition list (a path to JSON/XLSX, a loaded dict, or
        None for the shipped class defaults). ``inline_components="equipment"`` materialises each
        in-line valve as its own small equipment rather than recording it in the run's metadata --
        it decides what *exists*, which is why it is a read argument and deck bounds are not.

        **Nothing is dropped quietly.** Everything the read could not carry -- a segment whose ends
        the P&ID never named, an item that resolved to nothing placeable -- lands in :attr:`report`
        and is summarised in one warning; ``strict=True`` raises instead. Gaps found while
        *building* are a different failure with a different fix, and are reported separately on the
        assembly the build produces.
        """
        import pathlib as _pathlib

        from ada.cadit.dexpi.read.to_procedural import (
            DexpiImportReport,
            dexpi_to_procedural_doc,
        )
        from ada.cadit.dexpi.store import read_dexpi
        from ada.config import logger
        from ada.topo_model.build_spec import ProceduralBuildSpec

        document = read_dexpi(path, flavour=flavour)
        name = name or (document.header.project or _pathlib.Path(path).stem)

        def procedural_factory(spec):
            """The compiler's input for this P&ID, under ``spec``'s layout rules.

            Re-run per build rather than computed once: the decks and the equipment coordinates it
            contains are products of the build's ``LayoutRules``, so a second build with different
            deck bounds must get a different document from the same model.
            """
            return dexpi_to_procedural_doc(
                document,
                definitions=definitions,
                layout=spec.layout,
                base_doc=spec.base_doc,
                inline_components=inline_components,
            )

        # Resolved once for the native view with default rules. The rules cannot matter here --
        # nothing on the model carries a coordinate -- and the placements they produce are discarded
        # by _native_objects.
        doc, catalog = procedural_factory(ProceduralBuildSpec())
        report = DexpiImportReport.from_dict((doc.get("dexpi") or {}).get("report"))
        # Layout-stage gaps are dropped here, and that is not a convenience. Producing the native
        # view has to run the conversion, which runs the layout, which needs deck bounds -- so it
        # used a placeholder ``ProceduralBuildSpec()``. Any "no cell is large enough" it produced is
        # therefore about bounds nobody asked for, and keeping it would let a *read* report a
        # failure that only a *build* can have. The build reports its own, against real rules.
        report.issues = [issue for issue in report.issues if issue.stage != "layout"]
        equipment, systems = _native_objects(doc, catalog)

        model = cls(
            name=name,
            equipment=equipment,
            systems=systems,
            catalog=catalog,
            report=report,
            source_document=document,
            procedural_factory=procedural_factory,
            metadata={
                "source": str(path),
                "flavour": document.flavour.value,
                "reader_warnings": list(document.warnings),
                "report": report.as_dict(),
            },
        )

        if not report.is_clean:
            if strict:
                raise ValueError(f"DEXPI import of {_pathlib.Path(path).name}: {report.format()}")
            logger.warning("dexpi: %s (see model.report)", report.summary())
        return model

    # -- outputs ---------------------------------------------------------------------------------

    def to_assembly(self, spec: "ProceduralBuildSpec | None" = None) -> "Assembly":
        """Build a 3D model: generate decks, place the equipment on them, route the systems.

        ``spec`` (a :class:`~ada.topo_model.build_spec.ProceduralBuildSpec`) carries every choice
        the build makes -- deck bounds, design ruleset, structural blueprint, whether to route,
        whether to feed routing failures back into the layout. Defaults build a routed model with
        the standard rules.

        The result is a **new** assembly and this model is unchanged, so a second build with
        different rules starts from the same resolved input rather than from the first build's
        output.
        """
        from ada.topo_model.build_spec import ProceduralBuildSpec

        from .model_build import build_assembly

        return build_assembly(self, spec or ProceduralBuildSpec())

    def to_dexpi(
        self,
        destination: str | os.PathLike,
        *,
        flavour: str = "proteus",
        from_scratch: bool = False,
    ) -> pathlib.Path:
        """Write this model out as a DEXPI P&ID.

        The default is a **merge**, not a regeneration: it starts from :attr:`source_document` and
        re-serializes the equipment, ports and systems adapy owns from the live objects, so an edit
        made in Python lands in the output, while everything the source carried that adapy does not
        model -- the shape catalogue, presentation, attributes this branch does not touch -- is
        echoed back verbatim.

        ``from_scratch=True`` writes a brand-new document from the live objects alone. It is the
        only option for a model with no source document, and lossy by construction even for one
        that has it: there is no chamber, no piping class and no schematic drawing on the live
        objects to write back.
        """
        from ada.cadit.dexpi.write.from_ada import (
            write_model_from_scratch,
            write_model_merged,
        )

        destination = pathlib.Path(destination)
        if from_scratch:
            return write_model_from_scratch(self, destination, flavour=flavour)
        if self.source_document is None:
            raise ValueError(
                "to_dexpi(from_scratch=False) needs a source document on this model "
                "(SystemModel.source_document) -- only a reader such as ada.from_dexpi sets one. "
                "Pass from_scratch=True to write a new, adapy-only document instead."
            )
        return write_model_merged(self, destination, flavour=flavour)


def _native_objects(doc: dict, catalog: dict):
    """``(equipment, systems)`` as live adapy objects, with placement discarded.

    The procedural document has been through the layout, because that is the shape the compiler's
    input takes -- but a coordinate is not something the P&ID said, so none of it is carried onto
    the native model. Every piece of equipment is rebuilt at the origin and its ports come out of
    the catalog relative to its own box, which is exactly the frame a schematic has.
    """
    from ada.api.spatial.equipment import Equipment
    from ada.topo_model.compile import _equipment_to_object, _wire_systems
    from ada.topology.entities import TopoEquipment

    objects = []
    for row in doc.get("equipments") or []:
        entity = TopoEquipment(**{k: v for k, v in row.items() if v is not None})
        at_origin = entity.model_copy(update={"X": 0.0, "Y": 0.0, "Z": 0.0})
        objects.append(_equipment_to_object(at_origin, catalog.get))

    equipment = [obj for obj in objects if isinstance(obj, Equipment)]
    systems = _wire_systems(doc.get("systems") or [], {eq.name: eq for eq in equipment})
    return equipment, systems

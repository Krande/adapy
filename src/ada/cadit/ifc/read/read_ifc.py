from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.base.changes import ChangeAction
from ada.cadit.ifc.read.read_physical_objects import import_physical_ifc_elem
from ada.cadit.ifc.read.reader_utils import (
    add_to_assembly,
    get_ifc_property_sets,
    get_parent,
    resolve_name,
)
from ada.config import logger

from .read_materials import MaterialImporter
from .read_parts import PartImporter

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


@dataclass
class IfcReader:
    ifc_store: IfcStore

    def load_spatial_hierarchy(self):
        pi = PartImporter(self.ifc_store)
        pi.load_hierarchies()

    def load_materials(self):
        mi = MaterialImporter(self.ifc_store)
        mi.load_ifc_materials()

    def load_systems(self):
        """Reconstruct pipes from IfcDistributionSystem groupings.

        A Pipe is written as an IfcDistributionSystem grouping its IfcPipeSegment/IfcPipeFitting
        members (which the per-product pass skips). Build one Pipe per system from its members and
        add it to the spatial element the system services."""
        from ada import Pipe

        from .read_pipe import import_pipe_segment

        f = self.ifc_store.f
        assembly = self.ifc_store.assembly

        for system in f.by_type("IfcDistributionSystem"):
            members = []
            for rel in system.IsGroupedBy or []:
                members.extend(rel.RelatedObjects)
            seg_products = [m for m in members if m.is_a() in ("IfcPipeSegment", "IfcPipeFitting")]
            if not seg_products:
                continue

            segments = []
            for sp in seg_products:
                try:
                    segments.append(import_pipe_segment(sp, sp.Name, self.ifc_store))
                except Exception as e:  # noqa: BLE001 - one bad segment must not drop the pipe
                    logger.warning(f"Skipping pipe segment {sp.Name} of system {system.Name}: {e}")
            if not segments:
                continue

            # Build the Pipe and assign the imported segments directly — Pipe.from_segments would
            # rebuild the segments from points (losing the straight/elbow decomposition). The
            # centerline points are only for completeness; the segments carry the real geometry.
            points = [segments[0].p1, segments[-1].p2]
            pipe = Pipe(system.Name, points, segments[0].section, segments[0].material, guid=system.GlobalId)
            pipe._segments = segments
            for seg in segments:
                seg.parent = pipe

            parent = self._resolve_system_parent(system) or assembly
            parent.add_pipe(pipe)

    def resolve_weld_members(self):
        """Link each imported Weld back to its welded members by GUID.

        Members are persisted as GUIDs on the IfcFastener (read into ``weld.metadata``); they
        may be imported after the weld, so resolution runs as a post-pass."""
        import json

        from ada import Weld

        assembly = self.ifc_store.assembly
        for part in assembly.get_all_parts_in_assembly(include_self=True):
            for w in part.welds:
                if not isinstance(w, Weld):
                    continue
                raw = w.metadata.get("Properties", {}).get("members")
                if not raw:
                    continue
                resolved = [obj for obj in (assembly.get_by_guid(g) for g in json.loads(raw)) if obj is not None]
                if resolved:
                    w._members = tuple(resolved)

    def _resolve_system_parent(self, system):
        """The adapy Part that the system services (via IfcRelServicesBuildings → spatial elem)."""
        for rel in system.ServicesBuildings or []:
            for spatial in rel.RelatedBuildings:
                part = self.ifc_store.assembly.get_by_guid(spatial.GlobalId)
                if part is not None:
                    return part
        return None

    def load_presentation_layers(self):
        from ada.api.presentation_layers import PresentationLayer, PresentationLayers

        layers = dict()
        for obj in self.ifc_store.f.by_type("IfcPresentationLayerAssignment"):
            members = []
            for x in obj.AssignedItems:
                guid = None
                for inverse in self.ifc_store.f.get_inverse(x):
                    if inverse.is_a("IfcShapeRepresentation"):
                        product = inverse.OfProductRepresentation[0].ShapeOfProduct[0]
                        guid = product.GlobalId
                        break
                elem = self.ifc_store.assembly.get_by_guid(guid)
                if elem is None:
                    # raise ValueError()
                    continue
                if elem not in members:
                    members.append(elem)

            pl = PresentationLayer(
                obj.Name, obj.Description, members, identifier=obj.Identifier, change_type=ChangeAction.NOCHANGE
            )
            layers[pl.name] = pl

        self.ifc_store.assembly.presentation_layers = PresentationLayers(layers)

    def load_objects(self, data_only=False, elements2part=None):
        for product in self.ifc_store.f.by_type("IfcProduct"):
            if product.Representation is None or data_only is True:
                logger.info(f'Passing product "{product}"')
                continue

            parent = get_parent(product)
            name = product.Name

            props = get_ifc_property_sets(product)

            if name is None:
                name = resolve_name(props, product)

            logger.info(f"importing {name}")

            try:
                obj = import_physical_ifc_elem(product, name, self.ifc_store)
            except Exception as e:
                # A single unsupported/broken element must not abort import of the
                # entire model — log it and keep going.
                logger.warning(f'Skipping product "{name}" (#{product.id()}, {product.is_a()}): {e}')
                continue

            if obj is None:
                continue

            obj.metadata = props

            add_to_assembly(self.ifc_store.assembly, obj, parent, elements2part)

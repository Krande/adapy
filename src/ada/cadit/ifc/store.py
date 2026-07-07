from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import ifcopenshell
import ifcopenshell.geom

from ada.base.changes import ChangeAction
from ada.base.types import GeomRepr
from ada.base.units import Units
from ada.cadit.ifc.units_conversion import convert_file_length_units
from ada.cadit.ifc.utils import assembly_to_ifc_file, calculate_unit_scale, default_settings
from ada.cadit.ifc.write.write_user import create_owner_history_from_user
from ada.config import Config, logger

if TYPE_CHECKING:

    from ada import Assembly, Section, User
    from ada.cadit.ifc.read.read_ifc import IfcReader


def open_ifc_file(ifc_file_path: str | os.PathLike) -> ifcopenshell.file:
    """Open an IFC file, transparently decompressing a gzip-compressed model. Some exporters and
    servers ship a gzip'd IFC under a plain ``.ifc`` name; ``ifcopenshell.open`` then fails with
    'Unable to parse IFC SPF header'. Detect the gzip magic (0x1f 0x8b) and load the inflated SPF
    text via ``from_string`` instead."""
    import gzip

    path = str(ifc_file_path)
    with open(path, "rb") as fh:
        is_gzip = fh.read(2) == b"\x1f\x8b"
    if is_gzip:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return ifcopenshell.file.from_string(fh.read())
    return ifcopenshell.open(path)
    from ada.cadit.ifc.write.write_ifc import IfcWriter


@dataclass
class IfcStore:
    ifc_file_path: pathlib.Path | os.PathLike = None
    assembly: Assembly = None
    settings: ifcopenshell.geom.settings = field(default_factory=default_settings)

    f: ifcopenshell.file = None
    owner_history: ifcopenshell.entity_instance = None
    writer: IfcWriter = None
    reader: IfcReader = None
    callback: Callable[[int, int], None] | None = None

    def __getstate__(self):
        # ifcopenshell.file, ifcopenshell.geom.settings and entity_instance
        # are C-bound and don't pickle. Drop them on serialize; consumers
        # that unpickle an IfcStore directly get a stub and must reattach
        # via from_ifc() or by reading their assembly's .ifc_store. When
        # pickled through Assembly, Assembly.__getstate__ clears _ifc_store
        # before this is ever reached.
        state = self.__dict__.copy()
        state["f"] = None
        state["owner_history"] = None
        state["writer"] = None
        state["reader"] = None
        state["callback"] = None
        state["settings"] = None
        # Query caches hold C-bound entity_instance refs (don't pickle).
        state["_context_cache"] = {}
        state["_profile_def_cache"] = None
        state["_beam_type_cache"] = None
        state["_rel_defines_by_type_cache"] = None
        state["_rel_aggregates_cache"] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        if self.settings is None:
            self.settings = default_settings()
        self.reset_query_caches()

    def __post_init__(self):
        if self.f is None:
            if self.ifc_file_path is not None:
                self.ifc_file_path = pathlib.Path(self.ifc_file_path)
                if self.ifc_file_path.exists():
                    self.f = open_ifc_file(self.ifc_file_path)
            elif self.assembly is not None:
                self.f = assembly_to_ifc_file(self.assembly)
                self.add_standard_contexts()
            else:
                from ada import Assembly

                self.assembly = Assembly()
                self.f = assembly_to_ifc_file(self.assembly)
                self.add_standard_contexts()

        self.reset_query_caches()

    def reset_query_caches(self):
        # Per-sync memoization for what would otherwise be O(file) ``by_type``
        # scans run *per element*. On a 50k-element ship model that turns the
        # IFC export into an O(N²) crawl. Caches are keyed by the lookups the
        # write path needs (context identifier, profile/beam-type name,
        # relating entity id) and rebuilt lazily on first miss. ``sync()``
        # clears them up front so incremental re-exports stay correct.
        self._context_cache: dict = {}
        self._profile_def_cache: dict | None = None
        self._beam_type_cache: dict | None = None
        self._rel_defines_by_type_cache: dict | None = None
        self._rel_aggregates_cache: dict | None = None
        # Deferred IfcRelDefinesByType membership: many elements share one
        # relating type, and growing ``RelatedObjects`` per element re-walks
        # the whole aggregate each time (O(N²)). Members accumulate here as
        # plain Python lists and are written to the IFC aggregate once via
        # flush_rel_defines_by_type().
        self._rel_defines_pending: dict = {}

    def add_standard_contexts(self):
        contexts = list(self.f.by_type("IfcGeometricRepresentationContext"))
        model_context = list(filter(lambda x: x.ContextType.upper() == "MODEL", contexts))[0]
        for cid, target_view in [("Body", "MODEL_VIEW"), ("Axis", "GRAPH_VIEW"), ("Box", "MODEL_VIEW")]:
            self.f.create_entity(
                "IfcGeometricRepresentationSubContext",
                ContextIdentifier=cid,
                ContextType="Model",
                ParentContext=model_context,
                TargetView=target_view,
            )
        if Config().ifc_include_plan_context:
            plan_context = self.f.create_entity(
                "IfcGeometricRepresentationContext",
                ContextType="Plan",
                CoordinateSpaceDimension=2,
                Precision=1e-5,
                WorldCoordinateSystem=model_context.WorldCoordinateSystem,
            )
            for cid, target_view in [
                ("Axis", "GRAPH_VIEW"),
                ("Annotation", "PLAN_VIEW"),
                ("Annotation", "SECTION_VIEW"),
                ("Annotation", "ELEVATION_VIEW"),
            ]:
                self.f.create_entity(
                    "IfcGeometricRepresentationSubContext",
                    ContextIdentifier=cid,
                    ContextType="Plan",
                    ParentContext=plan_context,
                    TargetView=target_view,
                )

    def update_owner(self, user: User):
        self.owner_history = create_owner_history_from_user(user, self.f)

    def get_context(self, context_id):
        cached = self._context_cache.get(context_id)
        if cached is not None:
            return cached

        contexts = list(self.f.by_type("IfcGeometricRepresentationContext"))
        subcontexts = list(self.f.by_type("IfcGeometricRepresentationSubContext"))
        if len(contexts) == 1 and len(subcontexts) == 0:
            self._context_cache[context_id] = contexts[0]
            return contexts[0]

        matches = [x for x in contexts if x.ContextIdentifier == context_id and x.ContextType == "Model"]
        if len(matches) == 0:
            # Imported files don't always carry the standard subcontexts (e.g.
            # the buildingSMART type-library samples have '3D'/'2D' model+plan
            # contexts and nothing else). Writing into such a store must not
            # fail — create the missing subcontext under the model context,
            # exactly as add_standard_contexts would for a fresh file.
            model_ctxs = [
                x
                for x in contexts
                if not x.is_a("IfcGeometricRepresentationSubContext") and (x.ContextType or "").upper() == "MODEL"
            ]
            if not model_ctxs:
                raise ValueError(f'0 IfcGeometry Subcontexts found with "{context_id=}"')
            target_view = {"Axis": "GRAPH_VIEW"}.get(context_id, "MODEL_VIEW")
            sub = self.f.create_entity(
                "IfcGeometricRepresentationSubContext",
                ContextIdentifier=context_id,
                ContextType="Model",
                ParentContext=model_ctxs[0],
                TargetView=target_view,
            )
            self._context_cache[context_id] = sub
            return sub
        if len(matches) > 1:
            raise ValueError(f'Multiple Subcontexts found with "{context_id=}"')

        self._context_cache[context_id] = matches[0]
        return matches[0]

    def sync(
        self,
        include_fem=False,
        progress_callback: Callable[[int, int], None] = None,
        geom_repr_override: dict[str, GeomRepr] = None,
    ):
        from ada.cadit.ifc.write.write_ifc import IfcWriter

        self.writer = IfcWriter(self)
        self.writer.callback = progress_callback
        a = self.assembly

        # Drop any stale lookup caches from a previous sync; sections /
        # contexts created below repopulate them lazily on first access.
        self.reset_query_caches()

        a.consolidate_sections()
        a.consolidate_materials()

        self.update_owner(a.user)

        num_new_spatial_objects = self.writer.sync_spatial_hierarchy(include_fem=include_fem)

        self.writer.sync_sections()
        self.writer.sync_materials()

        num_new_objects = self.writer.sync_added_physical_objects()
        if geom_repr_override is not None:
            self.writer.sync_added_geom_repr()

        self.writer.sync_added_welds()

        self.writer.sync_mapped_instances()

        num_mod = self.writer.sync_modified_physical_objects()

        self.writer.sync_groups()
        self.writer.sync_presentation_layers()

        num_del = self.writer.sync_deleted_physical_objects()

        # Write all deferred IfcRelDefinesByType memberships in one pass.
        self.flush_rel_defines_by_type()

        # Drop relationships that ended up with no members (empty RelatedObjects
        # violates the schema's [1:?] cardinality) so the output validates clean.
        self.writer.prune_empty_relationships()

        add_str = f"Added {num_new_objects} objects and {num_new_spatial_objects} spatial elements"
        mod_str = f"Modified {num_mod} objects"
        del_str = f"Deleted {num_del} objects"

        logger.info(f"Sync Complete. {add_str}. {mod_str}. {del_str}")
        self.callback = None

    def save_to_file(self, filepath: str | os.PathLike):
        with open(filepath, "w") as f:
            f.write(self.f.wrapped_data.to_string())

    def load_ifc_content_from_file(
        self, ifc_file: str | os.PathLike | ifcopenshell.file = None, data_only=False, elements2part=None
    ) -> None:
        from ada.cadit.ifc.read.read_ifc import IfcReader

        if self.ifc_file_path is None:
            if ifc_file is None:
                raise ValueError("No ifc file is attached")
            if isinstance(ifc_file, (str, os.PathLike)):
                self.ifc_file_path = ifc_file
                self.f = IfcStore.ifc_obj_from_ifc_file(ifc_file)
            else:
                self.f = ifc_file

        if self.assembly is None:
            raise ValueError("Assembly must be attached before loading IFC content")

        self.reader = IfcReader(self)

        # Rebind the Assembly's guid to the IfcProject's stable GlobalId
        # so re-exports keep the same lineage anchor across roundtrips.
        # Without this, every IFC read mints a fresh Assembly.guid and a
        # derived GLB would have no stable id matching a previously
        # exported sibling.
        #
        # Assign through ``_guid`` directly rather than the property
        # setter: the setter assumes the assembly's current guid maps to
        # a real IFC entity and tries to rewrite that entity's GlobalId
        # (root.py:62-72). On a fresh read the auto-generated assembly
        # guid isn't in the file yet, so the setter's ``by_guid()``
        # lookup raises. We don't need that side-effect — the IFC
        # already carries the correct IfcProject.GlobalId; we're just
        # mirroring it onto the Assembly.
        projects = self.f.by_type("IfcProject")
        if projects and getattr(projects[0], "GlobalId", None):
            self.assembly._guid = projects[0].GlobalId

        # Compare raw length scales rather than mapping to the Units
        # enum — files in inches/feet (conversion-based units, e.g. the
        # buildingSMART samples at scale 0.0254) have no enum member but
        # convert_file_length_units handles them fine via ifcopenshell's
        # unit entities.
        unit_scale = calculate_unit_scale(self.f)  # meters per file length unit
        target_scale = 0.001 if self.assembly.units == Units.MM else 1.0
        if abs(unit_scale - target_scale) > 1e-9 * target_scale:
            self.f = convert_file_length_units(self.f, self.assembly.units)

        if elements2part is None:
            self.reader.load_spatial_hierarchy()

        # Load Materials
        self.reader.load_materials()

        # Load physical elements
        self.reader.load_objects(data_only=data_only, elements2part=elements2part)

        # Reconstruct pipes from IfcDistributionSystem groupings (segments were skipped above)
        self.reader.load_systems()

        # Link welds to their members by GUID (members may have been imported after the weld)
        self.reader.resolve_weld_members()

        self.reader.load_presentation_layers()

        ifc_file_name = "object" if self.ifc_file_path is None else self.ifc_file_path

        for obj in self.assembly.get_all_sections():
            obj.change_type = ChangeAction.NOCHANGE

        for obj in self.assembly.get_all_materials():
            obj.change_type = ChangeAction.NOCHANGE

        for obj in self.assembly.get_all_physical_objects():
            obj.change_type = ChangeAction.NOCHANGE

        for obj in self.assembly.get_all_parts_in_assembly(include_self=True):
            obj.change_type = ChangeAction.NOCHANGE

        logger.info(f'Import of IFC file "{ifc_file_name}" is complete')

    def get_ifc_geom(self, ifc_elem, settings: ifcopenshell.geom.settings):
        return ifcopenshell.geom.create_shape(settings, inst=ifc_elem)

    def get_ifc_geom_iterator(self, settings: ifcopenshell.geom.settings, cpus: int = None):
        import multiprocessing

        products = None
        if self.assembly is not None:
            products = []
            for x in self.assembly.get_all_physical_objects(pipe_to_segments=True):
                try:
                    product = self.f.by_guid(x.guid)
                except RuntimeError as e:
                    raise RuntimeError(f"{e} for {x}")
                products.append(product)
        cpus = multiprocessing.cpu_count() if cpus is None else cpus
        return ifcopenshell.geom.iterator(settings, self.f, cpus, include=products)

    def get_by_guid(self, guid: str) -> ifcopenshell.entity_instance:
        return self.f.by_guid(guid)

    def get_beam_type(self, section: Section, match_description=False) -> ifcopenshell.entity_instance | None:
        if self._beam_type_cache is None:
            self._beam_type_cache = {}
            for beam_type in self.f.by_type("IfcBeamType"):
                # First registered wins, matching the original linear scan.
                self._beam_type_cache.setdefault(beam_type.Name, beam_type)

        beam_type = self._beam_type_cache.get(section.name)
        if beam_type is None:
            logger.warning(f"Unable to find beam type for {section.name=}")
        return beam_type

    def get_profile_def(self, section: Section) -> ifcopenshell.entity_instance | None:
        if self._profile_def_cache is None:
            self._profile_def_cache = {}
            for p in self.f.by_type("IfcProfileDef"):
                name = getattr(p, "ProfileName", None)
                if name is not None:
                    self._profile_def_cache.setdefault(name, p)

        return self._profile_def_cache.get(section.name)

    def get_rel_defines_by_type(self, beam_type: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance | None:
        """Existing IfcRelDefinesByType whose RelatingType is ``beam_type`` (or None).

        Callers that create a fresh rel must register it via
        :meth:`register_rel_defines_by_type` so subsequent elements of the same
        section reuse it instead of re-scanning the file.
        """
        if self._rel_defines_by_type_cache is None:
            self._rel_defines_by_type_cache = {}
            for ifcrel in self.f.by_type("IfcRelDefinesByType"):
                rt = ifcrel.RelatingType
                if rt is not None:
                    self._rel_defines_by_type_cache.setdefault(rt.id(), ifcrel)

        return self._rel_defines_by_type_cache.get(beam_type.id())

    def register_rel_defines_by_type(
        self, beam_type: ifcopenshell.entity_instance, ifcrel: ifcopenshell.entity_instance
    ) -> None:
        if self._rel_defines_by_type_cache is None:
            self._rel_defines_by_type_cache = {}
        self._rel_defines_by_type_cache[beam_type.id()] = ifcrel

    def queue_rel_defines_by_type(
        self, beam_type: ifcopenshell.entity_instance, ifc_elem: ifcopenshell.entity_instance, name: str
    ) -> None:
        """Defer attaching ``ifc_elem`` to its type's IfcRelDefinesByType.

        Members are batched per relating type and written in one assignment by
        :meth:`flush_rel_defines_by_type`, avoiding the O(N²) aggregate re-walk
        of appending one element at a time.
        """
        entry = self._rel_defines_pending.get(beam_type.id())
        if entry is None:
            entry = {"beam_type": beam_type, "name": name, "elems": []}
            self._rel_defines_pending[beam_type.id()] = entry
        entry["elems"].append(ifc_elem)

    def flush_rel_defines_by_type(self) -> None:
        from ada.core.guid import create_guid

        for entry in self._rel_defines_pending.values():
            elems = entry["elems"]
            if not elems:
                continue
            existing = self.get_rel_defines_by_type(entry["beam_type"])
            if existing is not None:
                existing.RelatedObjects = tuple([*existing.RelatedObjects, *elems])
            else:
                new_rel = self.f.create_entity(
                    "IfcRelDefinesByType",
                    GlobalId=create_guid(),
                    OwnerHistory=self.owner_history,
                    Name=entry["name"],
                    Description=None,
                    RelatedObjects=elems,
                    RelatingType=entry["beam_type"],
                )
                self.register_rel_defines_by_type(entry["beam_type"], new_rel)
        self._rel_defines_pending = {}

    def get_rel_aggregates(self, relating_object: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance | None:
        """Existing IfcRelAggregates whose RelatingObject is ``relating_object`` (or None)."""
        if self._rel_aggregates_cache is None:
            self._rel_aggregates_cache = {}
            for rel_agg in self.f.by_type("IfcRelAggregates"):
                ro = rel_agg.RelatingObject
                if ro is not None:
                    self._rel_aggregates_cache.setdefault(ro.id(), rel_agg)

        return self._rel_aggregates_cache.get(relating_object.id())

    def register_rel_aggregates(
        self, relating_object: ifcopenshell.entity_instance, rel_agg: ifcopenshell.entity_instance
    ) -> None:
        if self._rel_aggregates_cache is None:
            self._rel_aggregates_cache = {}
        self._rel_aggregates_cache[relating_object.id()] = rel_agg

    @staticmethod
    def from_ifc(ifc_file: str | os.PathLike | ifcopenshell.file, make_a_copy=True) -> IfcStore:
        ifc_file_path = None

        if isinstance(ifc_file, (str, os.PathLike)):
            ifc_file_path = ifc_file
            f = IfcStore.ifc_obj_from_ifc_file(ifc_file)
        else:
            if make_a_copy:
                f = IfcStore.copy_ifc_obj(ifc_file)
            else:
                f = ifc_file

        return IfcStore(ifc_file_path=ifc_file_path, f=f)

    @staticmethod
    def ifc_obj_from_ifc_file(ifc_file: str | os.PathLike) -> ifcopenshell.file:
        ifc_file = pathlib.Path(ifc_file).resolve().absolute()
        if ifc_file.exists() is False:
            raise FileNotFoundError(f'Unable to find "{ifc_file}"')
        return open_ifc_file(ifc_file)

    @staticmethod
    def copy_ifc_obj(ifc_file: ifcopenshell.file) -> ifcopenshell.file:
        return ifcopenshell.file.from_string(ifc_file.wrapped_data.to_string())

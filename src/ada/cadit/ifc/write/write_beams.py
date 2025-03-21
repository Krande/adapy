from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada import Beam
from ada.api.beams import BeamRevolve, BeamSweep, BeamTapered
from ada.cadit.ifc.utils import convert_bm_jusl_to_ifc
from ada.cadit.ifc.write.beams.revolved_beam import create_revolved_beam
from ada.cadit.ifc.write.beams.straight_beam import extrude_straight_beam
from ada.cadit.ifc.write.beams.straight_beam_tapered import (
    extrude_straight_tapered_beam,
)
from ada.cadit.ifc.write.beams.swept_beam import create_swept_beam
from ada.config import logger
from ada.core.guid import create_guid

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def write_ifc_beam(ifc_store: IfcStore, beam: Beam):
    ibw = IfcBeamWriter(ifc_store)
    return ibw.create_ifc_beam(beam)


@dataclass
class IfcBeamWriter:
    ifc_store: IfcStore

    def create_ifc_beam(self, beam: Beam):
        if beam.parent is None:
            raise ValueError("Parent cannot be None for IFC export")

        f = self.ifc_store.f

        owner_history = self.ifc_store.owner_history
        profile = self.ifc_store.get_profile_def(beam.section)

        if isinstance(beam, BeamRevolve):
            axis, body, loc_plac = create_revolved_beam(beam, f, profile)
        elif isinstance(beam, BeamSweep):
            axis, body, loc_plac = create_swept_beam(beam, f, profile)
        elif isinstance(beam, BeamTapered):
            axis, body, loc_plac = extrude_straight_tapered_beam(beam, f, profile)
        else:
            axis, body, loc_plac = extrude_straight_beam(beam, f, profile)

        prod_def_shp = f.create_entity("IfcProductDefinitionShape", None, None, (axis, body))

        ifc_beam = f.create_entity(
            "IfcBeam",
            GlobalId=beam.guid,
            OwnerHistory=owner_history,
            Name=beam.name,
            Description=beam.section.sec_str,
            ObjectType="Beam",
            ObjectPlacement=loc_plac,
            Representation=prod_def_shp,
        )

        found_existing_relationship = False

        beam_type = self.ifc_store.get_beam_type(beam.section)
        if beam_type is None:
            raise ValueError()

        for ifcrel in f.by_type("IfcRelDefinesByType"):
            if ifcrel.RelatingType == beam_type:
                ifcrel.RelatedObjects = tuple([*ifcrel.RelatedObjects, ifc_beam])
                found_existing_relationship = True
                break

        if found_existing_relationship is False:
            f.create_entity(
                "IfcRelDefinesByType",
                GlobalId=create_guid(),
                OwnerHistory=owner_history,
                Name=beam.section.type.value,
                Description=None,
                RelatedObjects=[ifc_beam],
                RelatingType=beam_type,
            )

        self.add_material_assignment(beam, ifc_beam)

        return ifc_beam

    def add_material_assignment(self, beam: Beam, ifc_beam):
        sec = beam.section
        mat = beam.material
        ifc_store = self.ifc_store
        f = ifc_store.f

        ifc_mat_rel = ifc_store.f.by_guid(mat.guid)
        ifc_mat = ifc_mat_rel.RelatingMaterial

        ifc_profile = ifc_store.get_profile_def(beam.section)
        mat_profile = f.createIfcMaterialProfile(
            sec.name, "A material profile", ifc_mat, ifc_profile, None, "LoadBearing"
        )
        mat_profile_set = f.createIfcMaterialProfileSet(sec.name, None, [mat_profile], None)

        mat_usage = f.create_entity("IfcMaterialProfileSetUsage", mat_profile_set, convert_bm_jusl_to_ifc(beam))
        ifc_store.writer.create_rel_associates_material(create_guid(), mat_usage, [ifc_beam])

        # this is done as a post-step
        # ifc_store.writer.associate_elem_with_material(beam.material, ifc_beam)

        return mat_profile_set


def update_ifc_beam(ifc_store: IfcStore, beam: Beam):
    logger.warning("Updating IFC beam not implemented yet")

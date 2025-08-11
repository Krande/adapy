from dataclasses import dataclass

import numpy as np

from ada.api.transforms import Placement
from ada.core.vector_utils import is_parallel
from ada.sections import SectionCat
from ada.sections.concept import Section, SectionParts


@dataclass
class ShellProfileComp:
    type: str
    thick: float
    normal: np.ndarray


def get_profile_component_from_entity_cog_and_normal(
    section: Section, entity_cog, normal, placement: Placement
) -> ShellProfileComp:

    aligned_with_web = is_parallel(normal, placement.xdir)
    if aligned_with_web is True:
        return ShellProfileComp(SectionParts.WEB, section.t_w, normal)

    aligned_with_flange = is_parallel(normal, placement.ydir)
    if aligned_with_flange is True:
        # Project both points onto the Y-direction (local beam Up) and compare
        entity_z_coord = np.dot(np.array(entity_cog), placement.ydir)
        origin_z_coord = np.dot(placement.origin, placement.ydir)
        if entity_z_coord > origin_z_coord:
            return ShellProfileComp(SectionParts.TOP_FLANGE, section.t_ftop, normal)
        else:
            return ShellProfileComp(SectionParts.BTN_FLANGE, section.t_fbtn, normal)

    raise ValueError(f"Unable to identify {section.type} shell entity to correct section property")


def get_thick_normal_from_angular_beams(section: Section, cog, normal, placement: Placement) -> ShellProfileComp:
    aligned_with_web = is_parallel(normal, placement.xdir)
    if aligned_with_web is True:
        return ShellProfileComp(SectionParts.WEB, section.t_w, normal)
    else:
        return ShellProfileComp(SectionParts.BTN_FLANGE, section.t_fbtn, normal)


def get_thick_normal_from_box_beams(section: Section, cog, normal, placement: Placement) -> ShellProfileComp:
    aligned_with_web = is_parallel(normal, placement.xdir)
    if aligned_with_web is True:
        return ShellProfileComp(SectionParts.WEB, section.t_w, normal)
    else:
        return ShellProfileComp(SectionParts.BTN_FLANGE, section.t_fbtn, normal)


def get_thick_normal_from_channel_beams(section: Section, cog, normal, placement: Placement) -> ShellProfileComp:
    aligned_with_web = is_parallel(normal, placement.xdir)
    if aligned_with_web is True:
        return ShellProfileComp(SectionParts.WEB, section.t_w, normal)
    aligned_with_flange = is_parallel(normal, placement.ydir)
    if aligned_with_flange is True:
        cog_vec = placement.origin - np.array(cog)
        res = np.dot(placement.zdir, cog_vec)
        if res < 0:
            return ShellProfileComp(SectionParts.TOP_FLANGE, section.t_ftop, normal)
        else:
            return ShellProfileComp(SectionParts.BTN_FLANGE, section.t_fbtn, normal)

    raise ValueError(f"Unable to identify {section.type} shell entity to correct section property")


def get_thick_normal_from_flatbar_beams(section: Section, cog, normal, placement: Placement) -> ShellProfileComp:
    return ShellProfileComp(SectionParts.WEB, section.t_w, normal)


def eval_thick_normal_from_cog_of_beam_plate(section: Section, cog, normal, placement: Placement) -> ShellProfileComp:
    bt = SectionCat.BASETYPES

    eval_map = {
        bt.IPROFILE: get_profile_component_from_entity_cog_and_normal,
        bt.ANGULAR: get_thick_normal_from_angular_beams,
        bt.BOX: get_thick_normal_from_box_beams,
        bt.CHANNEL: get_thick_normal_from_channel_beams,
        bt.FLATBAR: get_thick_normal_from_flatbar_beams,
    }

    thick_identifier = eval_map.get(section.type, None)
    if thick_identifier is None:
        raise NotImplementedError("Not yet supported ")

    return thick_identifier(section, cog, normal, placement)

import pathlib
from typing import TYPE_CHECKING, Iterable

from ada.config import logger

if TYPE_CHECKING:
    from ada import Assembly, Part, Beam, Plate

def write_mac_file(assembly: Assembly, destination_file: str | pathlib.Path) -> None:
    mac_str = write_pml_str(assembly)
    with open(destination_file, "w") as f:
        f.write(mac_str)


def create_beam_pml(obj: Beam) -> str:
    beam_str = "new GENSEC"


    return beam_str

def create_plate_pml(obj: Plate) -> str:
    plate_str = ""

    return plate_str

def create_hierarchy(assembly: Assembly) -> str:
    """
    Create a hierarchy string for the assembly.
    """
    hierarchy_str = ""
    for obj in assembly.get_all_parts_in_assembly():
        ancestors = obj.get_ancestors()

    return hierarchy_str

def walk_hierarchy(part: Part) -> Iterable[Part]:
    for child in part.parts.values():
        yield child
        yield from walk_hierarchy(child)

def write_pml_str(assembly: Assembly) -> str:
    """
    Write a macro file for the assembly.
    """
    from ada import Beam, Plate

    mac_str = ""
    for part in walk_hierarchy(assembly):
        if isinstance(part, Assembly):
            mac_str += f"new site\n name {part.name}\n"
        elif isinstance(part, Part):
            ancestors = part.get_ancestors()
            level = len(ancestors)
            mac_str += create_hierarchy(part)
        else:
            logger.warning(f"Object of type {type(part)} not supported for pml generation.")

        for obj in part.get_all_parts_in_assembly():
            if isinstance(obj, Beam):
                mac_str += create_beam_pml(obj)
            elif isinstance(obj, Plate):
                mac_str += create_plate_pml(obj)
            else:
                logger.warning(f"Object of type {type(obj)} not supported for pml generation.")

    return mac_str

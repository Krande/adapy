import logging
from dataclasses import dataclass
from typing import List, Tuple, Union

from ada import FEM, Beam, Section
from ada.core.utils import Counter, make_name_fem_ready
from ada.fem import FemSection
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.shapes import ElemType

from .write_utils import write_ff

shid = Counter(1)
bmid = Counter(1)
sec_n = Counter(1, "_V")
concept = Counter(1)
concept_ircon = Counter(1)


@dataclass
class BmSectionStr:
    sec_str: str
    names_str: str
    tdsconc_str: str
    sconcept_str: str
    scon_mesh: str


def sections_str(fem: FEM, thick_map) -> str:

    sec_ids = []
    sec_str = ""
    names_str = ""
    concept_str = ""
    tdsconc_str, sconcept_str, scon_mesh = "", "", ""

    sec_names = []
    for fem_sec in fem.sections:
        if fem_sec.type == ElemType.LINE:
            res = create_line_section(fem_sec, sec_names, sec_ids)
            if res is None:
                continue
            names_str += res.names_str
            sec_str += res.sec_str
            tdsconc_str += res.tdsconc_str
            sconcept_str += res.sconcept_str
            scon_mesh += res.scon_mesh
        elif fem_sec.type == ElemType.SHELL:
            sec_str += create_shell_section_str(fem_sec, thick_map)
        elif fem_sec.type == ElemType.SOLID:
            sec_str += create_solid_section(fem_sec)
        else:
            raise IncompatibleElements(f"Solid element type {fem_sec.type} is not yet supported for writing to Sesam")

    return names_str + sec_str + concept_str + tdsconc_str + sconcept_str + scon_mesh


def create_shell_section_str(fem_sec: FemSection, thick_map) -> str:
    if fem_sec.thickness not in thick_map.keys():
        sh_id = next(shid)
        thick_map[fem_sec.thickness] = sh_id
    else:
        sh_id = thick_map[fem_sec.thickness]
    return write_ff("GELTH", [(sh_id, fem_sec.thickness, 5)])


def create_line_section(fem_sec: FemSection, sec_names: List[str], sec_ids: List[Section]) -> Union[BmSectionStr, None]:
    from .write_bm_profiles import write_bm_section

    sec = fem_sec.section
    if sec in sec_ids:
        logging.info(f'Skipping already included section "{sec}"')
        return None

    sec_ids.append(fem_sec.section)
    secid = next(bmid)
    sec_name = make_name_fem_ready(fem_sec.section.name, no_dot=True)
    if sec_name not in sec_names:
        sec_names.append(sec_name)
    else:
        sec_name += next(sec_n)

    sec.metadata["numid"] = secid

    names_str = write_ff(
        "TDSECT",
        [
            (4, secid, 100 + len(sec_name), 0),
            (sec_name,),
        ],
    )
    sec_str = write_bm_section(sec, secid)
    if fem_sec.refs is not None:
        tdsconc_str, sconcept_str, scon_mesh = create_sconcept_str(fem_sec)
    else:
        tdsconc_str, sconcept_str, scon_mesh = "", "", ""

    return BmSectionStr(
        sec_str=sec_str, names_str=names_str, tdsconc_str=tdsconc_str, sconcept_str=sconcept_str, scon_mesh=scon_mesh
    )


def create_solid_section(fem_sec: FemSection):
    raise IncompatibleElements(f"Solid element type {fem_sec.type} is not yet supported for writing to Sesam")


def create_sconcept_str(fem_sec: FemSection) -> Tuple[str, str, str]:
    sconcept_str = ""
    # Give concept relationship based on inputted values

    beams = [x for x in fem_sec.refs if type(x) is Beam]
    if len(beams) != 1:
        raise ValueError("A FemSection cannot be sourced from multiple beams")
    beam = beams[0]

    fem_sec.metadata["ircon"] = next(concept_ircon)
    bm_name = make_name_fem_ready(beam.name, no_dot=True)
    tdsconc_str = write_ff(
        "TDSCONC",
        [(4, fem_sec.metadata["ircon"], 100 + len(bm_name), 0), (bm_name,)],
    )
    sconcept_str += write_ff("SCONCEPT", [(8, next(concept), 7, 0), (0, 1, 0, 2)])
    sconc_ref = next(concept)
    sconcept_str += write_ff("SCONCEPT", [(5, sconc_ref, 2, 4), (1,)])
    elids: List[tuple] = []
    i = 0

    numel = len(beam.elem_refs)
    elid_bulk = [numel]
    for el in fem_sec.elset.members:
        if i == 3:
            elids.append(tuple(elid_bulk))
            elid_bulk = []
            i = -1
        elid_bulk.append(el.id)
        i += 1
    if len(elid_bulk) != 0:
        elids.append(tuple(elid_bulk))

    mesh_args = [(5 + numel, sconc_ref, 1, 2)] + elids
    scon_mesh = write_ff("SCONMESH", mesh_args)
    return tdsconc_str, sconcept_str, scon_mesh

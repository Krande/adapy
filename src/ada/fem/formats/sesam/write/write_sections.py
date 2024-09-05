from dataclasses import dataclass

from ada import FEM, Beam, Section
from ada.config import logger
from ada.core.utils import Counter, make_name_fem_ready
from ada.fem import FemSection
from ada.fem.exceptions.element_support import IncompatibleElements

from .write_utils import write_ff

shid = Counter(1)

sec_n = Counter(1, "_V")
concept = Counter(1)
concept_ircon = Counter(1)


@dataclass
class ConceptSection:
    sec_str: str = ""
    names_str: str = ""


@dataclass
class ConceptStructure:
    tdsconc_str: str = ""
    sconcept_str: str = ""
    scon_mesh: str = ""


def sections_str(fem: FEM, thick_map) -> str:
    sec_ids = []
    sec_str = ""
    names_str = ""
    concept_str = ""
    tdsconc_str, sconcept_str, scon_mesh = "", "", ""
    shid.set_i(max(fem.sections.id_map.keys()) + 1)
    sec_names = []
    for sh_sec in fem.sections.shells:
        sec_str += create_shell_section_str(sh_sec, thick_map)

    for fem_sec in fem.sections.lines:
        sec = create_line_section(fem_sec, sec_names, sec_ids)
        names_str += sec.names_str
        sec_str += sec.sec_str

        stru = create_sconcept_str(fem_sec)
        tdsconc_str += stru.tdsconc_str
        sconcept_str += stru.sconcept_str
        scon_mesh += stru.scon_mesh

    # TODO: Add support for solid elements
    for fem_sec in fem.sections.solids:
        sec_str += create_solid_section(fem_sec)

    return names_str + sec_str + concept_str + tdsconc_str + sconcept_str + scon_mesh


def create_shell_section_str(fem_sec: FemSection, thick_map) -> str:
    if fem_sec.thickness not in thick_map.keys():
        sh_id = next(shid)
        thick_map[fem_sec.thickness] = sh_id
    else:
        return ""

    number_of_integration_points = 5
    return write_ff("GELTH", [(sh_id, fem_sec.thickness, number_of_integration_points)])


def create_line_section(fem_sec: FemSection, sec_names: list[str], sec_ids: list[Section]) -> ConceptSection:
    from .write_bm_profiles import write_bm_section

    sec = fem_sec.section
    if sec in sec_ids:
        logger.info(f'Skipping already included section "{sec}"')
        return ConceptSection()

    sec_ids.append(sec)

    sec_name = make_name_fem_ready(sec.name, no_dot=True)
    if sec_name not in sec_names:
        sec_names.append(sec_name)
    else:
        sec_name += next(sec_n)

    names_str = write_ff(
        "TDSECT",
        [
            (4, sec.id, 100 + len(sec_name), 0),
            (sec_name,),
        ],
    )
    sec_str = write_bm_section(sec, sec.id)
    return ConceptSection(sec_str=sec_str, names_str=names_str)


def create_solid_section(fem_sec: FemSection):
    raise IncompatibleElements(f"Solid element type {fem_sec.type} is not yet supported for writing to Sesam")


def create_sconcept_str(fem_sec: FemSection) -> ConceptStructure:
    if fem_sec.refs is None:
        return ConceptStructure()

    sconcept_str = ""
    # Give concept relationship based on inputted values

    beams = [x for x in fem_sec.refs if isinstance(x, Beam)]
    if len(beams) != 1:
        raise ValueError("A FemSection cannot be sourced from multiple beams")
    beam = beams[0]

    ircon = next(concept_ircon)
    ircon_mesh = next(concept_ircon)

    fem_sec.metadata["ircon"] = ircon
    bm_name = make_name_fem_ready(beam.name, no_dot=True)
    tdsconc_str = write_ff(
        "TDSCONC",
        [(4, ircon, 100 + len(bm_name), 0), (bm_name,)],
    )

    sconcept_str += write_ff("SCONCEPT", [(8, ircon, 7, 0), (0, 1, 0, ircon_mesh)])
    sconcept_str += write_ff("SCONCEPT", [(5, ircon_mesh, 2, 4), (ircon,)])
    elids: list[tuple] = []
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

    mesh_args = [(5 + numel, ircon_mesh, 1, 2)] + elids
    scon_mesh = write_ff("SCONMESH", mesh_args)
    return ConceptStructure(tdsconc_str, sconcept_str, scon_mesh)

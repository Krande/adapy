from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic
from ada.fem.loads import Load

from ..names import concept_name
from ..write_loads import write_load

if TYPE_CHECKING:
    from ada.api.spatial import Part


@dataclass
class StatNonLin:
    name: str
    part: Part
    loads: list[Load]

    @property
    def sec_str(self):
        from ..write_constraints import has_cara_elem

        sec_str = ""
        if has_cara_elem(self.part):
            sec_str = "\n    CARA_ELEM=element,"
        return sec_str

    def get_bc_str(self):
        from ada.fem.exceptions.model_definition import NoBoundaryConditionsApplied

        from ..write_constraints import get_charge_names

        charges = get_charge_names(self.part)
        if len(charges) == 0:
            raise NoBoundaryConditionsApplied("No boundary condition is found for the specified model")

        return "".join(f"_F(CHARGE={name})," for name in charges)

    def get_load_str(self):
        # Every load of the step, each on the step's ramp: with the first alone the others were defined in the
        # deck and never applied.
        return ",".join(f"_F(CHARGE={concept_name(load, 'load')}, FONC_MULT=bc_step)" for load in self.loads)

    def write(self):
        return f"""{self.name} = STAT_NON_LINE(
    MODELE=model,
    CHAM_MATER=material,{self.sec_str}
    COMPORTEMENT=(_F(DEFORMATION="PETIT", TOUT="OUI")),
    CONVERGENCE=_F(ARRET="OUI", ITER_GLOB_MAXI=8,),
    EXCIT=({self.get_bc_str()}{self.get_load_str()}),
    INCREMENT=_F(LIST_INST=timeInst),
    ARCHIVAGE=_F(LIST_INST=timeReel),
)"""


@dataclass
class PostCalc:
    stat_non_line: StatNonLin
    part: Part

    def write(self):
        # EFGE (element forces) only makes sense for shell and beam elements, not for 3D solid elements
        has_solids = len(self.part.fem.sections.solids) > 0
        has_shells_or_beams = len(self.part.fem.sections.shells) > 0 or len(self.part.fem.sections.lines) > 0

        contrainte_fields = ["SIGM_ELNO"]
        if has_shells_or_beams:
            contrainte_fields.extend(["EFGE_ELNO", "EFGE_NOEU"])

        contrainte_str = ", ".join([f'"{field}"' for field in contrainte_fields])

        post_calc_str = f"""
{self.stat_non_line.name} = CALC_CHAMP(
    reuse={self.stat_non_line.name}, RESULTAT={self.stat_non_line.name},
    CONTRAINTE=({contrainte_str}),
    DEFORMATION=("EPSI_ELNO", "EPSP_ELNO"),
)"""
        if has_solids:
            return post_calc_str

        return (
            post_calc_str
            + """

stress = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
        NOM_CHAM=('SIGM_ELNO', ),
        NUME_COUCHE=1
    ),
    RESULTAT=result
)

stress = CALC_CHAMP(
    reuse=stress,
    CONTRAINTE=('SIGM_NOEU', ),
    RESULTAT=stress
)

strain = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
    NOM_CHAM=('EPSI_ELNO', ),
    NUME_COUCHE=1),
    RESULTAT=result
)

strainP = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
    NOM_CHAM=('EPSP_ELNO', ),
    NUME_COUCHE=1),
    RESULTAT=result
)"""
        )


@dataclass
class ImprResu:
    stat_nl: StatNonLin
    part: Part

    @property
    def post_calc_include(self):
        if len(self.part.fem.sections.solids) > 0:
            return ""

        return """_F(
            NOM_CHAM=("SIGM_ELNO", "SIGM_NOEU"),
            NOM_CHAM_MED=("STRESSES_ELEM", "STRESSES_NODES"),
            RESULTAT=stress,
        ),
        _F(
            NOM_CHAM=("EPSI_ELNO",),
            NOM_CHAM_MED=("STRAINS_ELEM",),
            RESULTAT=strain,
        ),
        _F(
            NOM_CHAM=("EPSP_ELNO",),
            NOM_CHAM_MED=("PLASTIC_STRAINS_ELEM",),
            RESULTAT=strainP,
        ),"""

    def write(self):
        # EFGE (element forces) only makes sense for shell and beam elements, not for 3D solid elements
        has_shells_or_beams = len(self.part.fem.sections.shells) > 0 or len(self.part.fem.sections.lines) > 0

        if has_shells_or_beams:
            result_nom_cham = '("DEPL", "EFGE_ELNO", "EFGE_NOEU")'
            result_nom_cham_med = '("DISP", "GEN_FORCES_ELEM", "GEN_FORCES_NODES")'
        else:
            result_nom_cham = '("DEPL",)'
            result_nom_cham_med = '("DISP",)'

        return f"""IMPR_RESU(
    RESU=(
        _F(
            NOM_CHAM={result_nom_cham},
            NOM_CHAM_MED={result_nom_cham_med},
            RESULTAT={self.stat_nl.name},
        ),
        {self.post_calc_include}
    ),
    UNITE=80,
)"""


def step_static_nonlin_str(step: StepImplicitStatic, part: Part) -> str:
    from ada.fem.exceptions.model_definition import NoLoadsApplied

    load_str = "\n".join(list(map(write_load, step.loads)))
    if len(step.loads) == 0:
        raise NoLoadsApplied(f"No loads are applied in step '{step}'")

    stat_non_line = StatNonLin("result", part, step.loads)
    stat_non_line_str = stat_non_line.write()
    post_calc = PostCalc(stat_non_line, part)
    post_calc_str = post_calc.write()
    iresu = ImprResu(stat_non_line, part)
    iresu_str = iresu.write()

    return f"""
{load_str}

timeReel = DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=10))
timeInst = DEFI_LIST_INST(METHODE="AUTO", DEFI_LIST=_F(LIST_INST=timeReel))
bc_step = DEFI_FONCTION(NOM_PARA="INST", VALE=(0.0, 0.0, 1.0, 1.0))

{stat_non_line_str}
{post_calc_str}
{iresu_str}
"""

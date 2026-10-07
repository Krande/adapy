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
    #: The step's own charge of prescribed values (it replaces the one holding them at zero); empty for none.
    prescribed: str = ""
    #: The step (for the supports in force in it); None: the model's supports.
    step: StepImplicitStatic | None = None

    @property
    def sec_str(self):
        from ..write_constraints import has_cara_elem

        sec_str = ""
        if has_cara_elem(self.part):
            sec_str = "\n    CARA_ELEM=element,"
        return sec_str

    def get_bc_str(self):
        from ada.fem.exceptions.model_definition import NoBoundaryConditionsApplied

        from ..write_constraints import step_charges

        supports = step_charges(self.part, self.step)
        charges = supports.names
        if len(charges) == 0:
            raise NoBoundaryConditionsApplied("No boundary condition is found for the specified model")
        if self.prescribed:
            charges = [c for c in charges if c != supports.zero]
        out = "".join(f"_F(CHARGE={name})," for name in charges)
        if self.prescribed:
            # ramped with the loads
            out += f"_F(CHARGE={self.prescribed}, FONC_MULT=bc_step),"
        return out

    def loads_str(self):
        # Every load of the step; only the first used to be applied.
        return "".join(f"_F(CHARGE={concept_name(ld, 'load')}, FONC_MULT=bc_step)," for ld in self.loads)

    def write(self):
        return f"""{self.name} = STAT_NON_LINE(
    MODELE=model,
    CHAM_MATER=material,{self.sec_str}
    COMPORTEMENT=(_F(DEFORMATION="PETIT", TOUT="OUI")),
    CONVERGENCE=_F(ARRET="OUI", ITER_GLOB_MAXI=8,),
    EXCIT=({self.get_bc_str()}{self.loads_str()}),
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
            + f"""

stress = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
        NOM_CHAM=('SIGM_ELNO', ),
        NUME_COUCHE=1
    ),
    RESULTAT={self.stat_non_line.name}
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
    RESULTAT={self.stat_non_line.name}
)

strainP = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
    NOM_CHAM=('EPSP_ELNO', ),
    NUME_COUCHE=1),
    RESULTAT={self.stat_non_line.name}
)"""
        )


@dataclass
class ImprResu:
    stat_nl: StatNonLin
    part: Part

    def med(self, *names: str) -> str:
        """The MED field names of this step's fields: as they were (``DISP``, ...) for the first result, prefixed with
        the result's name for the next (``result2_DISP``). Every nlgeom step printed ``DISP`` and the second stopped
        Code_Aster: "<MED2_4> Le champ 'DISP' est déjà présent dans le fichier MED" (measured, 18.1.8)."""
        prefix = "" if self.stat_nl.name == "result" else f"{self.stat_nl.name}_"
        return "(" + ", ".join(f'"{prefix}{n}"' for n in names) + ",)"

    @property
    def post_calc_include(self):
        if len(self.part.fem.sections.solids) > 0:
            return ""

        return f"""_F(
            NOM_CHAM=("SIGM_ELNO", "SIGM_NOEU"),
            NOM_CHAM_MED={self.med("STRESSES_ELEM", "STRESSES_NODES")},
            RESULTAT=stress,
        ),
        _F(
            NOM_CHAM=("EPSI_ELNO",),
            NOM_CHAM_MED={self.med("STRAINS_ELEM")},
            RESULTAT=strain,
        ),
        _F(
            NOM_CHAM=("EPSP_ELNO",),
            NOM_CHAM_MED={self.med("PLASTIC_STRAINS_ELEM")},
            RESULTAT=strainP,
        ),"""

    def write(self):
        # EFGE (element forces) only makes sense for shell and beam elements, not for 3D solid elements
        has_shells_or_beams = len(self.part.fem.sections.shells) > 0 or len(self.part.fem.sections.lines) > 0

        if has_shells_or_beams:
            result_nom_cham = '("DEPL", "EFGE_ELNO", "EFGE_NOEU")'
            result_nom_cham_med = self.med("DISP", "GEN_FORCES_ELEM", "GEN_FORCES_NODES")
        else:
            result_nom_cham = '("DEPL",)'
            result_nom_cham_med = self.med("DISP")

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


def step_static_nonlin_str(step: StepImplicitStatic, part: Part, result: str = "result", applied=()) -> str:
    from ada.fem.exceptions.model_definition import NoLoadsApplied
    from ada.fem.formats.prescribed import case_values

    from ..write_bc import prescribed_charge_str
    from ..write_constraints import step_charges
    from ..write_loads import STAGE

    loads = list(applied) + [ld for ld in step.loads if all(ld is not c for c in applied)]
    load_str = "\n".join(list(map(write_load, loads)))
    supports = step_charges(part, step)
    bcs = supports.bcs
    values = case_values(bcs, {step.name}, STAGE, step=step.name)
    prescribed = ""
    if any(v != 0.0 for v in values.values()):
        prescribed = f"{result}_pd"
        load_str += "\n" + prescribed_charge_str(prescribed, values, bcs)
    if len(loads) == 0 and not prescribed:
        raise NoLoadsApplied(f"No loads are applied in step '{step}'")

    from ada.fem.formats import conversion_report

    # Measured (tests/fem: two nlgeom steps, a 4 m strip whose tip deflects 0.55 and 1.66 m): the linear closed form
    # to 1e-10 -- DEFORMATION='PETIT' is small displacements, and RELATION defaults to 'ELAS'.
    conversion_report.current().approximated(
        STAGE,
        "STAT_NON_LINE",
        step.name,
        "a geometrically nonlinear step is solved with small displacements (DEFORMATION='PETIT') and an elastic "
        "material (RELATION 'ELAS', the default; a TRACTION curve is written but not used): the answer is the linear "
        "one",
    )
    stat_non_line = StatNonLin(result, part, loads, prescribed, step)
    stat_non_line_str = stat_non_line.write()
    post_calc = PostCalc(stat_non_line, part)
    post_calc_str = post_calc.write()
    iresu = ImprResu(stat_non_line, part)
    iresu_str = iresu.write()

    return f"""
{supports.definitions}
{load_str}

timeReel = DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=10))
timeInst = DEFI_LIST_INST(METHODE="AUTO", DEFI_LIST=_F(LIST_INST=timeReel))
bc_step = DEFI_FONCTION(NOM_PARA="INST", VALE=(0.0, 0.0, 1.0, 1.0))

{stat_non_line_str}
{post_calc_str}
{iresu_str}
"""

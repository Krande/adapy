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

    def comportement_str(self) -> str:
        """Small strains everywhere, but for the cables (``RELATION='CABLE'``, which Code_Aster solves with large
        displacements only) and, in a geometrically nonlinear step, the membranes (``ELAS_MEMBRANE_SV`` with
        ``GROT_GDEP``: a membrane carries out-of-plane load only as it deflects)."""
        out = '_F(DEFORMATION="PETIT", TOUT="OUI"),'
        sections = self.part.fem.sections
        nl_geom = self.step is not None and self.step.nl_geom is True
        membranes = [fs.elset.name for fs in sections.shells if fs.is_membrane]
        if nl_geom and membranes:
            # A membrane in GROT_GDEP takes a hyperelastic relation only (<MEMBRANE_3>, measured, 18.1.7);
            # Saint Venant-Kirchhoff is the one that is linear elastic at small strain.
            out += f'_F(RELATION="ELAS_MEMBRANE_SV", DEFORMATION="GROT_GDEP", GROUP_MA={_group_tuple(membranes)}),'
        cables = [fs.elset.name for fs in sections.lines if fs.is_cable]
        if cables:
            out += f'_F(RELATION="CABLE", DEFORMATION="GROT_GDEP", GROUP_MA={_group_tuple(cables)}),'
        return out

    @property
    def max_iterations(self) -> int:
        """Newton iterations per increment: 8, or 50 with membranes or cables in the model. A flat membrane loaded
        across itself starts from no out-of-plane stiffness and Newton converges slowly out of it -- measured
        (18.1.7), a pressurised square membrane pre-stretched through its edges took 31 iterations in its first
        increment and 2 to 5 in the next ones."""
        sections = self.part.fem.sections
        if any(fs.is_membrane for fs in sections.shells) or any(fs.is_cable for fs in sections.lines):
            return 50
        return 8

    def write(self):
        return f"""{self.name} = STAT_NON_LINE(
    MODELE=model,
    CHAM_MATER=material,{self.sec_str}
    COMPORTEMENT=({self.comportement_str()}),
    CONVERGENCE=_F(ARRET="OUI", ITER_GLOB_MAXI={self.max_iterations},),
    EXCIT=({self.get_bc_str()}{self.loads_str()}),
    INCREMENT=_F(LIST_INST=timeInst),
    ARCHIVAGE=_F(LIST_INST=timeReel),
)"""


def _group_tuple(names) -> str:
    return "(" + "".join(f"'{n}'," for n in names) + ")"


def _has_bending_shells(part: Part) -> bool:
    return any(not fs.is_membrane for fs in part.fem.sections.shells)


def _deformation_str(part: Part) -> str:
    """The strains to compute: on every element, unless the model has trusses, cables or membranes -- CABLE computes
    no ``EPSI_ELGA`` (<CALCUL_37>, measured, 18.1.7) -- where they are asked for on the beams, shells and solids
    only, and not at all without any."""
    sections = part.fem.sections
    special = [fs for fs in sections if fs.is_axial_only or fs.is_membrane]
    fields = '\n    DEFORMATION=("EPSI_ELNO", "EPSP_ELNO"),'
    if not special:
        return fields
    others = [fs.elset.name for fs in sections if not (fs.is_axial_only or fs.is_membrane)]
    if not others:
        return ""
    return f"{fields}\n    GROUP_MA={_group_tuple(others)},"


@dataclass
class PostCalc:
    stat_non_line: StatNonLin
    part: Part

    def write(self):
        # EFGE (element forces) only makes sense for shell and beam elements, not for 3D solid elements
        from .fields import stress_fields

        has_solids = len(self.part.fem.sections.solids) > 0

        sections = self.part.fem.sections
        if any(fs.is_axial_only or fs.is_membrane for fs in sections):
            # Trusses, cables and membranes compute SIEF_ELNO alone; each family is asked for what it computes.
            contrainte_fields = [f for f in stress_fields(self.part) if f != "SIPO_ELNO"]
        else:
            contrainte_fields = ["SIGM_ELNO"]
            if len(sections.shells) > 0 or len(sections.lines) > 0:
                contrainte_fields.extend(["EFGE_ELNO", "EFGE_NOEU"])
        contrainte_str = ", ".join([f'"{field}"' for field in contrainte_fields])

        post_calc_str = f"""
{self.stat_non_line.name} = CALC_CHAMP(
    reuse={self.stat_non_line.name}, RESULTAT={self.stat_non_line.name},
    CONTRAINTE=({contrainte_str}),{_deformation_str(self.part)}
)"""
        if has_solids or not _has_bending_shells(self.part):
            # The layer extraction is a shell's: a model of solids, or of beams, trusses, cables and membranes only,
            # has no layers to extract.
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
        if len(self.part.fem.sections.solids) > 0 or not _has_bending_shells(self.part):
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
        from .fields import stress_fields

        if "EFGE_ELNO" in stress_fields(self.part):
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

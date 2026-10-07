from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.api.spatial import Part
    from ada.fem.steps import FieldOutput, Step


def _create_field_output(
    field: FieldOutput, nl_geom, part: Part, result: str = "result", stress_targets: list[str] | None = None
) -> str:
    """The ``CALC_CHAMP`` calls of a step's result: reactions, then stresses and element forces.

    ``stress_targets`` are the keyword fragments (``NUME_ORDRE``, ``EXCIT``) of one stress call each; ``None`` is one
    call over the whole result. They are separate from the reactions because Code_Aster computes a beam model's
    stresses and element forces with one distributed load at most -- measured, 18.1.8: ``CONTRAINTE=("SIEF_ELNO",)``
    over a result whose load list held several stops at <CALCULEL2_92> ("votre chargement contient plus d'une charge
    répartie ... modèles de poutre"), where ``FORCE=("REAC_NODA",)`` over the same result ran.
    """
    # TODO: Make this a function of field output. Not randomly chosen hardcoded variables
    _ = {"S": "SIGM_ELNO"}

    # EFGE (element forces) only makes sense for shell and beam elements, not for 3D solid elements
    has_shells_or_beams = len(part.fem.sections.shells) > 0 or len(part.fem.sections.lines) > 0

    default_contrainte = ["SIGM_ELNO", "SIEF_ELNO", "SIPO_ELNO", "SIPM_ELNO"]
    if has_shells_or_beams:
        default_contrainte = ["EFGE_ELNO", "EFGE_NOEU"] + default_contrainte

    default_deformation = ["EPSI_ELNO", "EPSP_ELNO"]
    default_force = ["REAC_NODA"]

    defaults_def_str = ""
    defaults_c_str = ",".join([f'"{x}"' for x in default_contrainte])

    if nl_geom:
        defaults_def_str = "\n        DEFORMATION=(" + ",".join([f'"{x}"' for x in default_deformation]) + "),"

    defaults_forc_str = ",".join([f'"{x}"' for x in default_force])

    out = f"""
{result} = CALC_CHAMP(
        reuse={result}, RESULTAT={result},
        FORCE= ({defaults_forc_str}),
)\n"""
    for target in [""] if stress_targets is None else stress_targets:
        out += f"""
{result} = CALC_CHAMP(
        reuse={result}, RESULTAT={result},{target}
        CONTRAINTE=({defaults_c_str}),{defaults_def_str}
)\n"""
    return out


def _create_post_calc_nl_geom(step):
    return """stress = POST_CHAMP(
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


def final_writer(part: Part):
    # EFGE (element forces) only makes sense for shell and beam elements, not for 3D solid elements
    has_shells_or_beams = len(part.fem.sections.shells) > 0 or len(part.fem.sections.lines) > 0

    if has_shells_or_beams:
        efge_output = """_F(
            NOM_CHAM=("DEPL", "EFGE_ELNO", "EFGE_NOEU"),
            NOM_CHAM_MED=("DISP", "GEN_FORCES_ELEM", "GEN_FORCES_NODES"),
            RESULTAT=result,
        ),
        """
    else:
        efge_output = """_F(
            NOM_CHAM=("DEPL",),
            NOM_CHAM_MED=("DISP",),
            RESULTAT=result,
        ),
        """

    return f"""
    IMPR_RESU(
    RESU=(
        {efge_output}_F(
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
        ),
    ),
    UNITE=80,
)
"""


def create_field_output_str(
    step: Step, part: Part, result: str = "result", stress_targets: list[str] | None = None
) -> str:
    out_str = ""
    for f in step.field_outputs:
        out_str += _create_field_output(f, step.nl_geom, part, result, stress_targets)

    return out_str

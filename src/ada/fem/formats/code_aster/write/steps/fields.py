from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.api.spatial import Part
    from ada.fem.steps import Step


def stress_fields(part: Part) -> list[str]:
    """The ``CONTRAINTE`` fields of the model's element families: element forces (``EFGE``) on beams and shells,
    ``SIGM_ELNO`` on shells and solids, ``SIEF_ELNO`` on all, ``SIPO_ELNO`` (beam stresses per force component) on
    beams.

    Every model was asked for ``SIGM_ELNO``, ``SIEF_ELNO``, ``SIPO_ELNO`` and ``SIPM_ELNO``, and Code_Aster answers a
    field no element of the model computes with an alarm, so every run ended ``DIAGNOSTIC JOB : <A>_ALARM``. Measured,
    18.1.8: a shell-only model (DKT) <CALCULEL2_89> "Le champ SIPO_ELNO n'a pas pu être calculé" and the same for
    SIPM_ELNO; a beam-only model (POU_D_E) <CALCULEL2_89> for SIGM_ELNO and <ELEMENTS4_4> "L'option SIPM_ELNO n'est
    pas autorisée pour les sections de poutre de type GENERALE" -- adapy writes every beam section as ``GENERALE``
    (:mod:`..write_sections`), so ``SIPM_ELNO`` (extreme stresses over the section) is never computed and is not asked
    for."""
    sections = part.fem.sections
    beams, shells, solids = len(sections.lines) > 0, len(sections.shells) > 0, len(sections.solids) > 0
    out = ["EFGE_ELNO", "EFGE_NOEU"] if beams or shells else []
    if shells or solids:
        out.append("SIGM_ELNO")
    out.append("SIEF_ELNO")
    if beams:
        out.append("SIPO_ELNO")
    return out


def _create_field_output(nl_geom, part: Part, result: str = "result", stress_targets: list[str] | None = None) -> str:
    """The ``CALC_CHAMP`` calls of a step's result: reactions, then stresses and element forces
    (:func:`stress_fields`).

    ``stress_targets`` are the keyword fragments (``NUME_ORDRE``, ``EXCIT``) of one stress call each; ``None`` is one
    call over the whole result. They are separate from the reactions because Code_Aster computes a beam model's
    stresses and element forces with one distributed load at most -- measured, 18.1.8: ``CONTRAINTE=("SIEF_ELNO",)``
    over a result whose load list held several stops at <CALCULEL2_92> ("votre chargement contient plus d'une charge
    répartie ... modèles de poutre"), where ``FORCE=("REAC_NODA",)`` over the same result ran.
    """
    default_contrainte = stress_fields(part)

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
    """The ``CALC_CHAMP`` calls of a step's result, once for a step with any field output.

    The calls do not depend on the field output, and they were written once per ``FieldOutput``: a step with two (the
    concept step's and one added) asked for every field twice, and Code_Aster answered the second <CALCCHAMP_1> "Le
    champ EFGE_ELNO est déjà présent dans la structure de données" (measured, 18.1.8), an alarm per field."""
    if not step.field_outputs:
        return ""
    return _create_field_output(step.nl_geom, part, result, stress_targets)

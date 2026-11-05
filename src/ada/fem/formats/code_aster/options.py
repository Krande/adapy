from dataclasses import dataclass


@dataclass
class CodeAsterOptions:
    use_reduced_integration: bool = False
    default_shell_type: str = "QU4"
    #: Beams with shear deformation: ``POU_D_T`` (Timoshenko) with the section's shear coefficients, where the
    #: section has shear areas, instead of ``POU_D_E`` (Euler-Bernoulli). Sestra's, Abaqus' B31 and CalculiX's beams
    #: have shear deformation; set this to compare with them, or for short or deep beams.
    use_timoshenko_beams: bool = False
    #: The linear solver of the static steps (``SOLVEUR=_F(METHODE=...)``): ``"MUMPS"``, ``"MULT_FRONT"`` or
    #: ``"LDLT"``; None for Code_Aster's default (MUMPS, with ``RESI_RELA=1e-4`` on second-order shells). MULT_FRONT
    #: has no error estimate to stop at, where MUMPS's stops a stiff model at <FACTOR_57>.
    solver: str | None = None
    #: MUMPS's ``RESI_RELA`` with ``solver="MUMPS"``: the error estimate it stops above (Code_Aster's default 1e-6).
    resi_rela: float | None = None
    #: Also give the results as a Sesam Results Interface File, ``<name>R1.SIN``, for tools that read Sestra's (a code
    #: check of the plating, a SIN viewer): the deck is written with the Sesam deck of the same model beside it,
    #: ``<name>T1.FEM``, and a finished run's ``.rmed`` is converted (:mod:`.results.to_sin`).
    write_sin: bool = False

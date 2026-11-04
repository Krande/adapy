from dataclasses import dataclass


@dataclass
class CodeAsterOptions:
    use_reduced_integration: bool = False
    default_shell_type: str = "QU4"
    #: Beams with shear deformation: ``POU_D_T`` (Timoshenko) with the section's shear coefficients, where the
    #: section has shear areas, instead of ``POU_D_E`` (Euler-Bernoulli). Sestra's, Abaqus' B31 and CalculiX's beams
    #: have shear deformation; set this to compare with them, or for short or deep beams.
    use_timoshenko_beams: bool = False

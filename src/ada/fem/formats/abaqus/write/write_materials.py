from typing import TYPE_CHECKING

from ..grammar import format_number

#: The comment above *Elastic that carries a material's specific damping (``zeta``).
SPECIFIC_DAMPING = "Specific damping"

if TYPE_CHECKING:
    from ada import Assembly, Material


def materials_str(assembly: "Assembly"):
    all_mat = [mat for p in assembly.get_all_parts_in_assembly(True) for mat in p.materials]
    return "\n".join([material_str(mat) for mat in all_mat])


def material_str(material: "Material") -> str:
    """Every number exactly (``format_number``): ``{:.6E}``/``{:.5E}`` rounded the elastic
    modulus and every plastic stress, so a material read back was not the one written."""
    if "aba_inp" in material.metadata.keys():
        return material.metadata["aba_inp"]

    no_compression = material.metadata["no_compression"] if "no_compression" in material.metadata.keys() else False
    compr_str = "\n*No Compression" if no_compression is True else ""

    pl_str = ""
    if material.model.plasticity_model is not None:
        pl_model = material.model.plasticity_model
        if pl_model.eps_p is not None and len(pl_model.eps_p) != 0:
            pl_str = "\n*Plastic\n"
            pl_str += "\n".join(
                f"{format_number(x):>12}, {format_number(y):>10}" for x, y in zip(pl_model.sig_p, pl_model.eps_p)
            )

    alpha = material.model.rayleigh_damping.alpha
    beta = material.model.rayleigh_damping.beta
    d_str = ""
    if alpha is not None and beta is not None:
        d_str = f"\n*Damping, alpha={format_number(alpha)}, beta={format_number(beta)}"

    # *Expansion is the thermal expansion coefficient, ``alpha``. It used to be written from
    # ``zeta`` -- the material's damping -- so a model's damping became its thermal expansion.
    exp_str = ""
    if material.model.alpha is not None and material.model.alpha != 0.0:
        exp_str = f"\n*Expansion\n {format_number(material.model.alpha)}"

    # ``zeta`` is Sesam's "specific damping" (MISOSEL DAMP). Abaqus has no material property that
    # means that -- *Damping's COMPOSITE is a fraction of critical damping, STRUCTURAL a loss
    # factor -- so it is kept in a comment the reader reads, and not handed to the solver.
    zeta_str = ""
    if material.model.zeta is not None and material.model.zeta != 0.0:
        from ada.fem.formats import conversion_report

        zeta_str = f"\n** {SPECIFIC_DAMPING}: {format_number(material.model.zeta)}"
        conversion_report.current().note(
            "abaqus writer",
            "*MATERIAL",
            material.name,
            "specific damping (zeta) has no Abaqus material property; kept in a comment, not used by the solver",
        )

    # A massless material has no *Density (Abaqus rejects a zero density). Writing 1e-6 in its
    # place gave it a mass it did not have, and it read back as that.
    density_str = ""
    if material.model.rho > 0.0:
        density_str = f"\n*Density\n{format_number(material.model.rho)},"

    return (
        f"*Material, name={material.name}{zeta_str}\n*Elastic\n"
        f"{format_number(material.model.E)},  {format_number(material.model.v)}{compr_str}"
        f"{density_str}{exp_str}{d_str}{pl_str}"
    )

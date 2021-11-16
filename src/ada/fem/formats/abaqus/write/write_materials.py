from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Assembly, Material


def materials_str(assembly: "Assembly"):
    all_mat = [mat for p in assembly.get_all_parts_in_assembly(True) for mat in p.materials]
    return "\n".join([material_str(mat) for mat in all_mat])


def material_str(material: "Material") -> str:
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
                ["{x:>12.5E}, {y:>10}".format(x=x, y=y) for x, y in zip(pl_model.sig_p, pl_model.eps_p)]
            )

    alpha = material.model.rayleigh_damping.alpha
    beta = material.model.rayleigh_damping.beta
    d_str = ""
    if alpha is not None and beta is not None:
        d_str = f"\n*Damping, alpha={alpha}, beta={beta}"

    exp_str = ""
    if material.model.zeta is not None and material.model.zeta != 0.0:
        exp_str = f"\n*Expansion\n {material.model.zeta}"

    # Density == 0.0 is unsupported
    density = material.model.rho if material.model.rho > 0.0 else 1e-6

    return f"""*Material, name={material.name}
*Elastic
{material.model.E:.6E},  {material.model.v}{compr_str}
*Density
{density},{exp_str}{d_str}{pl_str}"""

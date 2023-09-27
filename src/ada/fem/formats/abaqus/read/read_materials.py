import re
from typing import TYPE_CHECKING

from ada.core.utils import roundoff
from ada.materials.concept import Material
from ada.materials.metals import CarbonSteel, PlasticityModel

from .helper_utils import _re_in

if TYPE_CHECKING:
    from ada import Assembly


def get_materials_from_bulk(assembly: "Assembly", bulk_str):
    re_str = (
        r"(\*Material,\s*name=.*?)(?=\*|\Z)(?!\*Elastic|\*Density|\*Plastic|"
        r"\*Damage Initiation|\*Damage Evolution|\*Expansion)"
    )
    re_materials = re.compile(re_str, _re_in)
    for m in re_materials.finditer(bulk_str):
        mat = mat_str_to_mat_obj(m.group())
        assembly.add_material(mat)


def mat_str_to_mat_obj(mat_str) -> Material:
    rd = roundoff

    # Name
    name = re.search(r"name=(.*?)\n", mat_str, _re_in).group(1).split("=")[-1].strip()

    # Density
    density_ = re.search(r"\*Density\n(.*?)(?:,|$)", mat_str, _re_in)
    if density_ is not None:
        density = rd(density_.group(1).strip().split(",")[0].strip(), 10)
    else:
        print('No density flag found for material "{}"'.format(name))
        density = None

    # Elastic
    re_elastic_ = re.search(r"\*Elastic(?:,\s*type=(.*?)|)\n(.*?)(?:\*|$)", mat_str, _re_in)
    if re_elastic_ is not None:
        re_elastic = re_elastic_.group(2).strip().split(",")
        young, poisson = rd(re_elastic[0]), rd(re_elastic[1])
    else:
        print('No Elastic properties found for material "{name}"'.format(name=name))
        young, poisson = None, None

    # Plastic
    re_plastic_ = re.search(r"\*Plastic\n(.*?)(?:\*|\Z)", mat_str, _re_in)
    if re_plastic_ is not None:
        re_plastic = [tuple(x.split(",")) for x in re_plastic_.group(1).strip().splitlines()]
        sig_p = [rd(x[0]) for x in re_plastic]
        eps_p = [rd(x[1]) for x in re_plastic]
    else:
        eps_p, sig_p = None, None

    # Expansion
    re_zeta = re.search(r"\*Expansion(?:,\s*type=(.*?)|)\n(.*?)(?:\*|$)", mat_str, _re_in)
    if re_zeta is not None:
        zeta = float(re_zeta.group(2).split(",")[0].strip())
    else:
        zeta = 0.0

    # Return material object
    model = CarbonSteel(
        rho=density,
        E=young,
        v=poisson,
        zeta=zeta,
        plasticity_model=PlasticityModel(eps_p=eps_p, sig_p=sig_p),
    )
    return Material(name=name, mat_model=model)

from typing import TYPE_CHECKING

from ada.config import logger
from ada.core.utils import roundoff
from ada.materials.concept import Material
from ada.materials.metals import CarbonSteel, PlasticityModel

from .keywords import validate
from .lexer import KeywordBlock, tokenize

if TYPE_CHECKING:
    from ada import Assembly

# The property blocks a *Material owns. Abaqus nests by adjacency: the material's definition
# runs until the next block that is not one of these.
_MATERIAL_PROPERTIES = (
    "DENSITY",
    "ELASTIC",
    "PLASTIC",
    "EXPANSION",
    "DAMAGE INITIATION",
    "DAMAGE EVOLUTION",
    "DEPVAR",
    "USER MATERIAL",
    "SPECIFIC HEAT",
    "CONDUCTIVITY",
)


def get_materials_from_bulk(assembly: "Assembly", bulk_str):
    blocks = tokenize(bulk_str)
    for i, block in enumerate(blocks):
        if block.keyword != "MATERIAL":
            continue
        validate(block)
        properties: dict[str, KeywordBlock] = {}
        for sub in blocks[i + 1 :]:
            if sub.keyword not in _MATERIAL_PROPERTIES:
                break
            # First wins: a repeated property block is a temperature/field table continuation
            # rather than a replacement, and only the first block carries the base values.
            properties.setdefault(sub.keyword, sub)
        assembly.add_material(_build_material(block, properties))


def _first_value(block: KeywordBlock | None, column: int = 0):
    if block is None or not block.data_lines:
        return None
    values = [x.strip() for x in block.data_lines[0].split(",")]
    if column >= len(values) or values[column] == "":
        return None
    return values[column]


def _build_material(block: KeywordBlock, properties: dict[str, KeywordBlock]) -> Material:
    rd = roundoff
    name = block.params.get("NAME")

    density_block = properties.get("DENSITY")
    if density_block is not None and _first_value(density_block) is not None:
        density = rd(_first_value(density_block), 10)
    else:
        logger.warning('No density flag found for material "{}"'.format(name))
        density = None

    elastic_block = properties.get("ELASTIC")
    young = poisson = None
    if elastic_block is not None and elastic_block.data_lines:
        young, poisson = _first_value(elastic_block), _first_value(elastic_block, 1)
        young = rd(young) if young is not None else None
        poisson = rd(poisson) if poisson is not None else None
    if young is None and poisson is None:
        logger.warning('No Elastic properties found for material "{name}"'.format(name=name))

    plastic_block = properties.get("PLASTIC")
    eps_p = sig_p = None
    if plastic_block is not None and plastic_block.data_lines:
        rows = [tuple(x.split(",")) for x in plastic_block.data_lines]
        sig_p = [rd(x[0]) for x in rows]
        eps_p = [rd(x[1]) for x in rows]

    expansion_block = properties.get("EXPANSION")
    zeta_value = _first_value(expansion_block)
    zeta = float(zeta_value) if zeta_value is not None else 0.0

    # Return material object. Only pass mechanical properties that the deck actually
    # specified — a material with no *Elastic / *Density (e.g. a user-material or a deck
    # that defines them elsewhere) then keeps CarbonSteel's defaults rather than carrying
    # None, which would crash every downstream writer (IFC/Sesam materials) on float(None).
    mat_kwargs = dict(zeta=zeta, plasticity_model=PlasticityModel(eps_p=eps_p, sig_p=sig_p))
    if density is not None:
        mat_kwargs["rho"] = density
    if young is not None:
        mat_kwargs["E"] = young
    if poisson is not None:
        mat_kwargs["v"] = poisson
    model = CarbonSteel(**mat_kwargs)
    return Material(name=name, mat_model=model)

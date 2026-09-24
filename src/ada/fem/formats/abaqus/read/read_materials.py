from typing import TYPE_CHECKING

from ada.config import logger
from ada.materials.concept import Material
from ada.materials.metals import CarbonSteel, PlasticityModel

from .keywords import validate
from .lexer import KeywordBlock, mark_read, tokenize

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
    # Not data-bearing, but part of the material: *No Compression ended the material early
    # (it was not in this list), so a *Density or *Expansion after it was never read.
    "NO COMPRESSION",
    "DAMPING",
)


def get_materials_from_bulk(assembly: "Assembly", bulk_str):
    mark_read("MATERIAL", *_MATERIAL_PROPERTIES)
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
    """Values as written, as floats. They went through ``roundoff`` (a fixed number of
    decimals), which changed an elastic modulus or a plastic stress on every read."""
    name = block.params.get("NAME")

    density_block = properties.get("DENSITY")
    if density_block is not None and _first_value(density_block) is not None:
        density = float(_first_value(density_block))
    else:
        # No *Density is a massless material -- Abaqus's own reading of it.
        logger.warning('No density flag found for material "{}"'.format(name))
        density = 0.0

    elastic_block = properties.get("ELASTIC")
    young = poisson = None
    if elastic_block is not None and elastic_block.data_lines:
        young, poisson = _first_value(elastic_block), _first_value(elastic_block, 1)
        young = float(young) if young is not None else None
        poisson = float(poisson) if poisson is not None else None
    if young is None and poisson is None:
        logger.warning('No Elastic properties found for material "{name}"'.format(name=name))

    plastic_block = properties.get("PLASTIC")
    eps_p = sig_p = None
    if plastic_block is not None and plastic_block.data_lines:
        rows = [tuple(x.split(",")) for x in plastic_block.data_lines]
        sig_p = [float(x[0]) for x in rows]
        eps_p = [float(x[1]) for x in rows]

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

    damping_block = properties.get("DAMPING")
    if damping_block is not None:
        alpha, beta = damping_block.params.get("ALPHA"), damping_block.params.get("BETA")
        model.rayleigh_damping.alpha = float(alpha) if alpha is not None else None
        model.rayleigh_damping.beta = float(beta) if beta is not None else None

    metadata = {"no_compression": True} if "NO COMPRESSION" in properties else {}
    return Material(name=name, mat_model=model, metadata=metadata)

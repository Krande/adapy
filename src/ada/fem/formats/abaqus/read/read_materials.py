from typing import TYPE_CHECKING

from ada.config import logger
from ada.core.utils import roundoff
from ada.materials.concept import Material
from ada.materials.metals import CarbonSteel, PlasticityModel

from .keywords import validate
from .lexer import Card, tokenize

if TYPE_CHECKING:
    from ada import Assembly

# The property cards a *Material owns. Abaqus nests by adjacency: the material's definition
# runs until the next card that is not one of these.
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
    cards = tokenize(bulk_str)
    for i, card in enumerate(cards):
        if card.keyword != "MATERIAL":
            continue
        validate(card)
        properties: dict[str, Card] = {}
        for sub in cards[i + 1 :]:
            if sub.keyword not in _MATERIAL_PROPERTIES:
                break
            # First wins: a repeated property card is a temperature/field table continuation
            # rather than a replacement, and only the first block carries the base values.
            properties.setdefault(sub.keyword, sub)
        assembly.add_material(_build_material(card, properties))


def _first_value(card: Card | None, column: int = 0):
    if card is None or not card.data_lines:
        return None
    values = [x.strip() for x in card.data_lines[0].split(",")]
    if column >= len(values) or values[column] == "":
        return None
    return values[column]


def _build_material(card: Card, properties: dict[str, Card]) -> Material:
    rd = roundoff
    name = card.params.get("NAME")

    density_card = properties.get("DENSITY")
    if density_card is not None and _first_value(density_card) is not None:
        density = rd(_first_value(density_card), 10)
    else:
        logger.warning('No density flag found for material "{}"'.format(name))
        density = None

    elastic_card = properties.get("ELASTIC")
    young = poisson = None
    if elastic_card is not None and elastic_card.data_lines:
        young, poisson = _first_value(elastic_card), _first_value(elastic_card, 1)
        young = rd(young) if young is not None else None
        poisson = rd(poisson) if poisson is not None else None
    if young is None and poisson is None:
        logger.warning('No Elastic properties found for material "{name}"'.format(name=name))

    plastic_card = properties.get("PLASTIC")
    eps_p = sig_p = None
    if plastic_card is not None and plastic_card.data_lines:
        rows = [tuple(x.split(",")) for x in plastic_card.data_lines]
        sig_p = [rd(x[0]) for x in rows]
        eps_p = [rd(x[1]) for x in rows]

    expansion_card = properties.get("EXPANSION")
    zeta_value = _first_value(expansion_card)
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

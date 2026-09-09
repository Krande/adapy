"""Unit handling for DEXPI attribute values.

The two flavours name units differently -- Proteus writes ``Units="Millimetre"`` (sometimes with an
RDL ``UnitsURI`` alongside it), DEXPI 2.0 writes an enumeration reference
``Core/PhysicalQuantities.LengthUnit.Millimetre``. :func:`normalize_unit` folds all of those to one
lowercase key, and the tables below turn that key into SI.

Only a curated set of units is covered: the ones a P&ID actually carries. An unrecognised unit
returns None rather than guessing, and the caller is expected to keep the raw ``(value, unit)``
pair -- adapy converts to SI where it models the quantity and echoes the original everywhere else.

Nominal diameter gets its own functions because DN and NPS are *designators*, not measurements:
DN 50 pipe has a 60.3 mm outside diameter. :func:`dn_to_m` returns the designator read as
millimetres, which is what a routing centreline wants.
"""

from __future__ import annotations

import re

__all__ = [
    "dn_to_m",
    "mm_to_m",
    "nominal_diameter_to_m",
    "normalize_unit",
    "nps_to_m",
    "parse_nominal_diameter",
    "si_factor",
    "to_si",
]

# Normalized unit key -> (factor, offset) such that ``si = value * factor + offset``.
_SI_UNITS: dict[str, tuple[float, float]] = {
    # length
    "metre": (1.0, 0.0),
    "millimetre": (1e-3, 0.0),
    "centimetre": (1e-2, 0.0),
    "micrometre": (1e-6, 0.0),
    "kilometre": (1e3, 0.0),
    "inch": (0.0254, 0.0),
    "foot": (0.3048, 0.0),
    # area / volume
    "metresquared": (1.0, 0.0),
    "millimetresquared": (1e-6, 0.0),
    "metrecubed": (1.0, 0.0),
    "litre": (1e-3, 0.0),
    # mass
    "kilogram": (1.0, 0.0),
    "gram": (1e-3, 0.0),
    "tonne": (1e3, 0.0),
    # pressure (SI base: pascal)
    "pascal": (1.0, 0.0),
    "kilopascal": (1e3, 0.0),
    "megapascal": (1e6, 0.0),
    "bar": (1e5, 0.0),
    "millibar": (1e2, 0.0),
    "poundforcepersquareinch": (6894.757293168361, 0.0),
    # temperature (SI base: kelvin)
    "kelvin": (1.0, 0.0),
    "degreecelsius": (1.0, 273.15),
    # flow
    "metrecubedpersecond": (1.0, 0.0),
    "metrecubedperhour": (1.0 / 3600.0, 0.0),
    "kilogrampersecond": (1.0, 0.0),
    "kilogramperhour": (1.0 / 3600.0, 0.0),
    # power / frequency
    "watt": (1.0, 0.0),
    "kilowatt": (1e3, 0.0),
    "megawatt": (1e6, 0.0),
    "reciprocalsecond": (1.0, 0.0),
    "reciprocalminute": (1.0 / 60.0, 0.0),
    # angle
    "radian": (1.0, 0.0),
    "degree": (0.017453292519943295, 0.0),
}

# Abbreviations that turn up in Proteus exports, mapped onto the keys above.
_UNIT_ALIASES: dict[str, str] = {
    "m": "metre",
    "mm": "millimetre",
    "cm": "centimetre",
    "km": "kilometre",
    "um": "micrometre",
    "in": "inch",
    "inches": "inch",
    "ft": "foot",
    "feet": "foot",
    "m2": "metresquared",
    "mm2": "millimetresquared",
    "m3": "metrecubed",
    "l": "litre",
    "kg": "kilogram",
    "g": "gram",
    "t": "tonne",
    "pa": "pascal",
    "kpa": "kilopascal",
    "mpa": "megapascal",
    "psi": "poundforcepersquareinch",
    "k": "kelvin",
    "c": "degreecelsius",
    "degc": "degreecelsius",
    "celsius": "degreecelsius",
    "m3s": "metrecubedpersecond",
    "m3h": "metrecubedperhour",
    "kgs": "kilogrampersecond",
    "kgh": "kilogramperhour",
    "w": "watt",
    "kw": "kilowatt",
    "mw": "megawatt",
    "rpm": "reciprocalminute",
    "rad": "radian",
    "deg": "degree",
    # plural/US spellings
    "meter": "metre",
    "millimeter": "millimetre",
    "centimeter": "centimetre",
    "micrometer": "micrometre",
    "kilometer": "kilometre",
    "metersquared": "metresquared",
    "metercubed": "metrecubed",
    "metercubedperhour": "metrecubedperhour",
    "liter": "litre",
    "ton": "tonne",
}

# NPS designator (inches) -> DN designator (millimetres), ASME B36.10M / ISO 6708.
_NPS_TO_DN: dict[float, int] = {
    0.125: 6,
    0.25: 8,
    0.375: 10,
    0.5: 15,
    0.75: 20,
    1.0: 25,
    1.25: 32,
    1.5: 40,
    2.0: 50,
    2.5: 65,
    3.0: 80,
    3.5: 90,
    4.0: 100,
    5.0: 125,
    6.0: 150,
    8.0: 200,
    10.0: 250,
    12.0: 300,
    14.0: 350,
    16.0: 400,
    18.0: 450,
    20.0: 500,
    22.0: 550,
    24.0: 600,
    26.0: 650,
    28.0: 700,
    30.0: 750,
    32.0: 800,
    34.0: 850,
    36.0: 900,
    40.0: 1000,
    42.0: 1050,
    48.0: 1200,
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
_FRACTION = re.compile(r"(\d+)\s*/\s*(\d+)")


def normalize_unit(unit: str | None) -> str | None:
    """Fold a unit reference of any flavour to a lookup key.

    Handles the DEXPI 2.0 enumeration form, an RDL URI, and a plain Proteus ``Units`` string::

        normalize_unit("Core/PhysicalQuantities.LengthUnit.Millimetre") -> "millimetre"
        normalize_unit("http://data.posccaesar.org/rdl/Millimetre")     -> "millimetre"
        normalize_unit("mm")                                           -> "millimetre"
    """
    if unit is None:
        return None

    token = unit.strip()
    if not token:
        return None

    # URI -> last meaningful segment; enumeration reference -> the member name. The slash is only
    # treated as a separator when it clearly is one: "m3/h" and "kg/s" are unit strings, not paths.
    if "#" in token:
        token = token.rsplit("#", 1)[-1]
    if "://" in token:
        token = token.rstrip("/").rsplit("/", 1)[-1]
    if "/" in token and "." in token.rsplit("/", 1)[-1]:
        token = token.rsplit("/", 1)[-1]
    if "." in token:
        token = token.rsplit(".", 1)[-1]

    key = _NON_ALNUM.sub("", token.lower())
    if not key:
        return None
    key = _UNIT_ALIASES.get(key, key)
    return key if key in _SI_UNITS else None


def si_factor(unit: str | None) -> float | None:
    """Multiplicative factor from ``unit`` to its SI base, or None if unrecognised.

    Careful with offset units: ``si_factor("DegreeCelsius")`` is 1.0 and says nothing about the
    273.15 offset. Use :func:`to_si` unless you know the unit is purely multiplicative.
    """
    key = normalize_unit(unit)
    return _SI_UNITS[key][0] if key else None


def to_si(value: float | None, unit: str | None) -> float | None:
    """Convert ``value`` from ``unit`` to its SI base unit. None if either input is unusable."""
    if value is None:
        return None
    key = normalize_unit(unit)
    if key is None:
        return None
    factor, offset = _SI_UNITS[key]
    return value * factor + offset


def mm_to_m(value: float | None) -> float | None:
    """Millimetres to metres -- the conversion the schematic frame needs on every read."""
    return None if value is None else value * 1e-3


def dn_to_m(dn: float | None) -> float | None:
    """DN designator to metres, i.e. the designator read as millimetres (DN 50 -> 0.05)."""
    return None if dn is None else float(dn) * 1e-3


def nps_to_m(nps: float | None) -> float | None:
    """NPS designator (inches) to metres, via the standard NPS->DN table.

    Falls back to a plain inch conversion for a size outside the table, which keeps an unusual
    designator roughly right rather than dropping it.
    """
    if nps is None:
        return None
    dn = _NPS_TO_DN.get(float(nps))
    if dn is not None:
        return dn * 1e-3
    return float(nps) * 0.0254


def nominal_diameter_to_m(value: float | None, kind: str | None = None) -> float | None:
    """Interpret a nominal-diameter number according to its type representation.

    ``kind`` is what DEXPI calls the *NominalDiameterTypeRepresentation* -- ``"DN"``, ``"NPS"``,
    ``"IN"``, ``"MM"``. It defaults to DN, which is what a metric P&ID omitting the type means.
    """
    if value is None:
        return None

    token = (kind or "DN").strip().lower()
    if token in ("nps", '"', "inch", "inches", "in"):
        return nps_to_m(value)
    if token in ("mm", "millimetre", "millimeter"):
        return mm_to_m(value)
    if token in ("m", "metre", "meter"):
        return float(value)
    return dn_to_m(value)


def parse_nominal_diameter(text: str | None, kind: str | None = None) -> float | None:
    """Parse a nominal-diameter *representation* string to metres.

    Accepts the shapes emitters actually write -- ``"DN 50"``, ``"DN50"``, ``"50"``, ``"NPS 2"``,
    ``'2"'``, ``'1 1/2"'``. Returns None when no number can be found.
    """
    if text is None:
        return None

    token = text.strip()
    if not token:
        return None

    lowered = token.lower()
    resolved = kind
    if resolved is None:
        if "nps" in lowered or '"' in token or "inch" in lowered:
            resolved = "NPS"
        elif lowered.startswith("dn"):
            resolved = "DN"

    body = re.sub(r"(?i)\b(dn|nps|nb)\b", " ", token).replace('"', " ")

    fraction = _FRACTION.search(body)
    if fraction is not None:
        whole = _NUMBER.search(body[: fraction.start()])
        value = float(fraction.group(1)) / float(fraction.group(2))
        if whole is not None:
            value += float(whole.group())
        return nominal_diameter_to_m(value, resolved)

    number = _NUMBER.search(body)
    if number is None:
        return None
    return nominal_diameter_to_m(float(number.group()), resolved)

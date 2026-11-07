"""Temperature loads (:class:`~ada.fem.LoadTemperature`) as Code_Aster's command variable TEMP.

Code_Aster takes a temperature as a field of the material field (``AFFE_MATERIAU(AFFE_VARC=_F(NOM_VARC='TEMP',
CHAM_GD=..., VALE_REF=...))``, U4.43.03), with the thermal expansion ``ALPHA`` in ``DEFI_MATERIAU``'s ``ELAS``: every
mechanical solve of the model then takes the thermal strain ``ALPHA (TEMP - VALE_REF)`` -- the temperature is the
model's, not a step's. So:

* the field (``temp_f``, ``CREA_CHAMP NOEU_TEMP_R``) is the reference everywhere, then each load's temperature on its
  nodes (``GROUP_NO``) or its elements' (``GROUP_MA``), or on the whole mesh;
* the steps that carry temperature loads must all carry the same ones, and every load one reference: otherwise a step
  would be solved with another step's temperature, refused by name;
* a temperature load is not a charge, so it is kept out of the steps' ``EXCIT`` (:func:`mechanical`);
* on a model with shells the field also holds the shells' ``TEMP_MIL``, ``TEMP_INF`` and ``TEMP_SUP`` (a uniform
  temperature: all three the same).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.exceptions.model_definition import UnsupportedLoadType

if TYPE_CHECKING:
    from ada.api.spatial import Part

#: The temperature field's concept name.
FIELD = "temp_f"


def is_temperature(load) -> bool:
    return getattr(load, "type", None) == "temperature"


def mechanical(loads) -> list:
    """``loads`` without the temperature loads (which act through the material field, not as charges)."""
    return [ld for ld in loads if not is_temperature(ld)]


def temperature_loads(steps) -> list:
    """The model's temperature loads: those of the steps that carry any, which must all carry the same ones."""
    per_step = [(st, [ld for ld in st.loads if is_temperature(ld)]) for st in steps]
    with_temp = [(st, lds) for st, lds in per_step if lds]
    if not with_temp:
        return []
    first_step, first = with_temp[0]
    key = sorted((ld.name, ld.temperature, ld.reference) for ld in first)
    for st, lds in per_step:
        if sorted((ld.name, ld.temperature, ld.reference) for ld in lds) != key:
            raise UnsupportedLoadType(
                f"code_aster writer: step {st.name!r} has other temperature loads than step {first_step.name!r}; a "
                "Code_Aster temperature is the material field's, the same in every step"
            )
    if len({ld.reference for ld in first}) > 1:
        raise UnsupportedLoadType("code_aster writer: temperature loads with different reference temperatures")
    return first


def temperature_field_str(part: Part, loads, mesh: str = "mesh") -> tuple[str, str]:
    """``(definitions, AFFE_VARC item)`` of the model's temperature loads; empty strings for none."""
    if not loads:
        return "", ""
    # A shell takes its temperature through the thickness, TEMP_MIL / TEMP_INF / TEMP_SUP (mid-plane, the faces on
    # the normal's negative and positive side); a uniform temperature is the same on all three. A field holding them
    # beside TEMP serves beams, solids and shells alike.
    shells = any(not fs.is_membrane for fs in part.fem.sections.shells)
    cmps = "('TEMP', 'TEMP_MIL', 'TEMP_INF', 'TEMP_SUP')" if shells else "'TEMP'"

    def vale(t: float) -> str:
        return f"({t!r}, {t!r}, {t!r}, {t!r})" if shells else repr(t)

    ref = loads[0].reference
    rows = [f"_F(TOUT='OUI', NOM_CMP={cmps}, VALE={vale(ref)}),"]
    for ld in loads:
        fs = ld.fem_set
        where = (
            "TOUT='OUI'" if fs is None else (f"GROUP_NO='{fs.name}'" if fs.type == "nset" else f"GROUP_MA='{fs.name}'")
        )
        rows.append(f"_F({where}, NOM_CMP={cmps}, VALE={vale(ld.temperature)}),  # {ld.name}")
    body = "\n        ".join(rows)
    definitions = f"""{FIELD} = CREA_CHAMP(
    TYPE_CHAM='NOEU_TEMP_R',
    OPERATION='AFFE',
    MAILLAGE={mesh},
    AFFE=(
        {body}
    ),
)
"""
    varc = f"""
    AFFE_VARC=_F(TOUT='OUI', NOM_VARC='TEMP', CHAM_GD={FIELD}, VALE_REF={ref!r}),"""
    return definitions, varc

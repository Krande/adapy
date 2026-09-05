"""Model-property fields: what the elements ARE, painted like a result.

Thickness, material and section are input data the deck already carries and
the reader already parses (GELTH, MISOSEL/TDMATER, the profile cards and
TDSECT). Emitting them as ordinary element-average fields reuses the whole
result pipeline — blobs, ranges, flat per-element colouring, picking — while
``category: "property"`` keeps them out of the results pickers: an inspect
panel lists the category, the results tree hides it.

The stored value of a categorical field (material, section) is the deck's own
id; what the id MEANS travels as ``value_labels`` on the presentation so a
legend can print "S355" rather than "3". Ids, not dense codes, so a picked
element's readout matches the deck the analyst knows.

Every field is a single step: properties do not vary by load case.
"""

from __future__ import annotations

import numpy as np

from ada.fem.results.field_data import (
    ElementFieldData,
    FieldPosition,
    FieldPresentation,
)

#: The namespace is the category contract — ``_classify_field`` maps it to
#: ``"property"``.
THICKNESS_FIELD = "props.thickness"
MATERIAL_FIELD = "props.material"
SECTION_FIELD = "props.section"

PROPERTY_FIELD_NAMES = (THICKNESS_FIELD, MATERIAL_FIELD, SECTION_FIELD)


def _element_average_rows(labels: np.ndarray, values: np.ndarray) -> np.ndarray:
    rows = np.empty((len(labels), 3), dtype=float)
    rows[:, 0] = labels
    rows[:, 1] = 1  # single "integration point": the element itself
    rows[:, 2] = values
    return rows


def _field(
    step: int,
    name: str,
    component: str,
    elem_type,
    labels: np.ndarray,
    values: np.ndarray,
    *,
    group_label: str,
    unit: str = "",
    value_labels: tuple[tuple[float, str], ...] = (),
) -> ElementFieldData:
    return ElementFieldData(
        name=name,
        step=int(step),
        components=[component],
        values=_element_average_rows(labels, values),
        elem_type=elem_type,
        field_pos=FieldPosition.ELEMENT_AVERAGE,
        int_positions=[(0, "element")],
        presentation=FieldPresentation(
            semantic_key=name,
            group_path=("Properties", group_label),
            coordinate_system="",
            surface="",
            derived=False,
            unit=unit,
            component_units=(unit,) if unit else (),
            value_labels=value_labels,
        ),
    )


def build_property_fields(mesh, sif, *, wanted: set[str] | None = None, step: int = 1) -> list[ElementFieldData]:
    """One element-average field per property per element type.

    ``mesh`` is the reader's :class:`Mesh` (blocks, ``elem_data``, section and
    material tables); ``sif`` supplies the GELTH thickness map. ``wanted``
    follows ``get_sif_results``' contract: ``None`` means everything.
    """

    def _wanted(name: str) -> bool:
        return wanted is None or name in wanted

    if not any(_wanted(name) for name in PROPERTY_FIELD_NAMES):
        return []

    elem_data = getattr(mesh, "elem_data", None)
    if elem_data is None or len(elem_data) == 0:
        return []

    # elem_data columns: (elno, matno, geono, transno).
    ref = np.asarray(elem_data, dtype=float)
    matno_by_elno = {int(row[0]): int(row[1]) for row in ref}
    geono_by_elno = {int(row[0]): int(row[2]) for row in ref}

    thickness_by_geono = sif.get_shell_thickness_map() if _wanted(THICKNESS_FIELD) else {}
    materials = getattr(mesh, "materials", None) or {}
    sections = getattr(mesh, "sections", None) or {}

    out: list[ElementFieldData] = []
    for block in mesh.elements:
        labels = np.asarray(block.identifiers, dtype=int)
        if labels.size == 0:
            continue
        elem_type = block.elem_info.type
        is_line = type(elem_type).__name__ == "LineShapes"

        geonos = np.array([geono_by_elno.get(int(label), 0) for label in labels], dtype=int)
        matnos = np.array([matno_by_elno.get(int(label), 0) for label in labels], dtype=int)

        if _wanted(THICKNESS_FIELD) and not is_line:
            th = np.array([thickness_by_geono.get(int(g), np.nan) for g in geonos], dtype=float)
            if np.isfinite(th).any():
                out.append(
                    _field(
                        step,
                        THICKNESS_FIELD,
                        "TH",
                        elem_type,
                        labels,
                        th,
                        group_label="THICKNESS",
                        unit="m",
                    )
                )

        if _wanted(MATERIAL_FIELD) and (matnos > 0).any():
            used = sorted({int(m) for m in matnos if m > 0})
            value_labels = tuple(
                (float(mat_id), getattr(materials.get(mat_id), "name", None) or f"M{mat_id}") for mat_id in used
            )
            out.append(
                _field(
                    step,
                    MATERIAL_FIELD,
                    "MATERIAL",
                    elem_type,
                    labels,
                    np.where(matnos > 0, matnos.astype(float), np.nan),
                    group_label="MATERIAL",
                    value_labels=value_labels,
                )
            )

        if _wanted(SECTION_FIELD) and is_line and (geonos > 0).any():
            used = sorted({int(g) for g in geonos if g > 0})
            value_labels = tuple(
                (float(sec_id), getattr(sections.get(sec_id), "name", None) or f"S{sec_id}") for sec_id in used
            )
            out.append(
                _field(
                    step,
                    SECTION_FIELD,
                    "SECTION",
                    elem_type,
                    labels,
                    np.where(geonos > 0, geonos.astype(float), np.nan),
                    group_label="SECTION",
                    value_labels=value_labels,
                )
            )

    return out


__all__ = [
    "build_property_fields",
    "PROPERTY_FIELD_NAMES",
    "THICKNESS_FIELD",
    "MATERIAL_FIELD",
    "SECTION_FIELD",
]

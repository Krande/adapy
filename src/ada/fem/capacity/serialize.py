"""Serialize a :class:`CapacityModel` to a Genie-compatible ``model.json`` mirror.

The mirror mimics the subset of Genie's ``model.json`` schema needed for
field-by-field validation (geometry, material, section, element ids), so a
derived model can be diffed against the reference ``Cc2.run1/model.json``.
"""

from __future__ import annotations

import json
import pathlib

from ada.fem.capacity.model import CapacityModel, CapMaterial, CapSection


def _material(m: CapMaterial) -> dict:
    return {
        "E": m.E,
        "G": m.G if m.G is not None else m.E / (2.0 * (1.0 + m.poisson)),
        "fy": m.fy,
        "GammaM": m.gamma_m,
        "PoissonRatio": m.poisson,
    }


def _section(s: CapSection) -> dict:
    return {
        "SectionName": s.name,
        "SectionType": s.section_type,
        "SectionParameters": {
            "Height": s.height,
            "WebThickness": s.web_thickness,
            "FlangeWidth": s.flange_width,
            "FlangeThickness": s.flange_thickness,
        },
    }


def to_genie_dict(models: list[CapacityModel]) -> dict:
    buckling_models = []
    for i, m in enumerate(models, start=1):
        plates = [
            {
                "Id": j,
                "Name": p.name,
                "Type": 0,
                "Material": _material(p.material),
                "Geometry": {"Thickness": p.thickness, "Length": p.length, "Width": p.width},
                "FiniteElements": list(p.element_ids),
            }
            for j, p in enumerate(m.plates, start=1)
        ]
        stiffeners = [
            {
                "Id": j,
                "Name": s.name,
                "Type": 1,
                "Material": _material(s.material),
                "Sections": [_section(s.section)],
                "FiniteElements": list(s.element_ids),
                "SupportAtFirstCrossSection": 0 if s.continuous else 1,
                "SupportAtSecondCrossSection": 0 if s.continuous else 1,
            }
            for j, s in enumerate(m.stiffeners, start=1)
        ]
        buckling_models.append(
            {"Id": i, "Name": m.name, "Type": 1, "Plates": plates, "Stiffeners": stiffeners}
        )
    return {"BucklingModels": buckling_models}


def write_genie_json(path: str | pathlib.Path, models: list[CapacityModel]) -> None:
    pathlib.Path(path).write_text(json.dumps(to_genie_dict(models), indent=2), encoding="utf-8")

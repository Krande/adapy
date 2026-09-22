"""Generates ``plant-a_v1.ifc`` with adapy's OWN writer. The output is committed (small; run this
file to regenerate it, which nothing in CI does automatically -- the committed file IS the fixture).

Shape (Decision 4's corpus spec): 2 ``IfcSite``, each with 2 ``IfcBuildingStorey``, one storey
holding an ``IfcElementAssembly``, ~40 beams and plates total, depth >= 4.

**Why the two sites are nested (SiteB under SiteA) rather than siblings under IfcProject.**
``ada.Assembly`` is itself always written as exactly one ``IfcSite`` (``SpatialWriter.
create_ifc_site()`` in ``write_spatial_elements.py`` hard-codes the entity type, ignoring
``ifc_class`` -- there is no adapy Part-tree shape that puts two independent ``IfcSite``s directly
under ``IfcProject``). Rather than fight that by surgically rewiring ``IfcRelAggregates`` after the
fact, the assembly's own always-present site IS SiteA, and SiteB is an ordinary child ``Part`` of
``ifc_class=IfcSite`` beneath it. The result is still exactly 2 reachable ``IfcSite`` entities --
which is what "declared roots default to every IfcSite" and the whole-file publish acceptance ("2
site manifests") actually count -- just nested one level rather than sibling. Depth is unaffected
(``StoreyA2 -> AssemblyAA -> beam`` alone already clears depth >= 4 from ``IfcProject``).
"""

from __future__ import annotations

import pathlib

import ada
from ada.base.ifc_types import SpatialTypes

HERE = pathlib.Path(__file__).parent
OUTPUT = HERE / "plant-a_v1.ifc"


def _beams(prefix: str, n: int, z: float, x0: float) -> list[ada.Beam]:
    return [ada.Beam(f"{prefix}-bm{i}", (x0 + i * 2.0, 0.0, z), (x0 + i * 2.0, 5.0, z), "IPE200") for i in range(n)]


def _plates(prefix: str, n: int, z: float, x0: float) -> list[ada.Plate]:
    out = []
    for i in range(n):
        x = x0 + i * 2.0
        out.append(
            ada.Plate(f"{prefix}-pl{i}", [(x, 0.0), (x + 1.5, 0.0), (x + 1.5, 1.5), (x, 1.5)], 0.01, origin=(0, 0, z))
        )
    return out


def build_plant_a() -> ada.Assembly:
    # The assembly root doubles as SiteA -- see the module docstring.
    a = ada.Assembly("SiteA", project="plant-a")

    storey_a1 = ada.Part("StoreyA1", ifc_class=SpatialTypes.IfcBuildingStorey)
    storey_a1 / (_beams("a1", 6, 0.0, 0.0) + _plates("a1", 4, 0.0, 0.0))

    storey_a2 = ada.Part("StoreyA2", ifc_class=SpatialTypes.IfcBuildingStorey)
    assembly_aa = ada.Part("AssemblyAA", ifc_class=SpatialTypes.IfcElementAssembly)
    assembly_aa / (_beams("aa", 6, 3.0, 0.0) + _plates("aa", 4, 3.0, 0.0))
    storey_a2 / [assembly_aa]

    site_b = ada.Part("SiteB", ifc_class=SpatialTypes.IfcSite)
    storey_b1 = ada.Part("StoreyB1", ifc_class=SpatialTypes.IfcBuildingStorey)
    storey_b1 / (_beams("b1", 6, 0.0, 20.0) + _plates("b1", 4, 0.0, 20.0))
    storey_b2 = ada.Part("StoreyB2", ifc_class=SpatialTypes.IfcBuildingStorey)
    storey_b2 / (_beams("b2", 6, 3.0, 20.0) + _plates("b2", 4, 3.0, 20.0))
    site_b / [storey_b1, storey_b2]

    a / [storey_a1, storey_a2, site_b]
    return a


def main() -> None:
    a = build_plant_a()
    a.to_ifc(destination=OUTPUT, file_obj_only=False, validate=True)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()

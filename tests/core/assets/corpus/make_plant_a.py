"""Generates the ``plant-a`` IFC corpus with adapy's OWN writer. Outputs are committed (small; run
this file to regenerate them, which nothing in CI does automatically -- the committed files ARE
the fixtures):

* ``plant-a_v1.ifc`` -- Phase 3's corpus, UNCHANGED in shape (Decision 4's spec): 2 ``IfcSite``,
  each with 2 ``IfcBuildingStorey``, one storey holding an ``IfcElementAssembly``, ~40 beams and
  plates total, depth >= 4.
* ``plant-a_v2.ifc`` -- the SAME model at a later instant with exactly ONE member modified, ONE
  removed, ONE added, one storey untouched (``StoreyA1``), one site untouched (``SiteB``).
* ``plant-a_v2-leaf.ifc`` -- a single-product file: one already-published member (``a1-bm0``,
  UNCHANGED between v1 and v2) republished standalone, for the leaf-without-stem publish
  (``--leaf --source``, Phase 4).

**Why every named entity gets a DETERMINISTIC, name-keyed guid.** ``ada``'s writer mints a random
``GlobalId`` per object by default (``ada.core.guid.create_guid()``), which is fine for a single
export but wrong for a CORPUS whose whole point is that v1 and v2 are two INDEPENDENT exports of
the same underlying plant: the Phase-4 sweep and the leaf-without-stem publish both work by GUID
IDENTITY (``ada.assets.ifc.walk``/``sweep``: "by GUID presence, and by a hash ... recorded in
``ifc.index.json``"), so a member that persists across the two files must keep the SAME id, and
only a genuinely new member should mint a new one. ``create_guid(name=...)`` (``ada/core/guid.py``)
is already exactly this: an md5-derived, deterministic, valid 22-character GlobalId keyed by a
name string -- calling it with the same name in both generators reproduces the same id, and a name
neither file shares (``aa-bm6``, v2-only) reproduces a fresh one, all without hand-maintaining a
guid table.
"""

from __future__ import annotations

import pathlib

import ada
from ada.base.ifc_types import SpatialTypes
from ada.core.guid import create_guid

HERE = pathlib.Path(__file__).parent
OUTPUT_V1 = HERE / "plant-a_v1.ifc"
OUTPUT_V2 = HERE / "plant-a_v2.ifc"
OUTPUT_V2_LEAF = HERE / "plant-a_v2-leaf.ifc"


def _guid(name: str) -> str:
    """The one guid a given NAME ever gets, in any generator in this module."""
    return create_guid(name=name)


def _beam(name: str, z: float, x: float, *, length: float = 5.0, sec: str = "IPE200") -> ada.Beam:
    return ada.Beam(name, (x, 0.0, z), (x, length, z), sec, guid=_guid(name))


def _plate(name: str, x: float, z: float) -> ada.Plate:
    return ada.Plate(
        name, [(x, 0.0), (x + 1.5, 0.0), (x + 1.5, 1.5), (x, 1.5)], 0.01, origin=(0, 0, z), guid=_guid(name)
    )


def _beams(prefix: str, n: int, z: float, x0: float) -> list[ada.Beam]:
    return [_beam(f"{prefix}-bm{i}", z, x0 + i * 2.0) for i in range(n)]


def _plates(prefix: str, n: int, z: float, x0: float) -> list[ada.Plate]:
    return [_plate(f"{prefix}-pl{i}", x0 + i * 2.0, z) for i in range(n)]


def _part(name: str, ifc_class: SpatialTypes) -> ada.Part:
    return ada.Part(name, ifc_class=ifc_class, guid=_guid(name))


def build_plant_a(*, revise: bool = False) -> ada.Assembly:
    """The whole ``plant-a`` model. ``revise=False`` builds v1; ``revise=True`` builds v2, which
    changes ONLY ``AssemblyAA`` under ``StoreyA2`` -- ``StoreyA1`` and the whole of ``SiteB`` are
    built by the exact same calls as v1, so their subtrees are byte-for-byte identical (same
    names, same guids, same geometry) and the sweep must find nothing to say about them.

    The assembly root doubles as SiteA -- see the module's original docstring reasoning (adapy's
    ``Assembly`` is always written as exactly one ``IfcSite``, so two real sibling sites are not
    reachable through the public API; SiteB nests one level under SiteA instead of beside it,
    which does not change what "declared roots default to every IfcSite" counts).
    """
    a = ada.Assembly("SiteA", project="plant-a")
    a.guid = _guid("SiteA")  # set post-construction: Assembly.__init__ takes no guid= of its own

    storey_a1 = _part("StoreyA1", SpatialTypes.IfcBuildingStorey)
    storey_a1 / (_beams("a1", 6, 0.0, 0.0) + _plates("a1", 4, 0.0, 0.0))

    storey_a2 = _part("StoreyA2", SpatialTypes.IfcBuildingStorey)
    assembly_aa = _part("AssemblyAA", SpatialTypes.IfcElementAssembly)
    aa_members: list = []
    for i in range(6):
        name = f"aa-bm{i}"
        if revise and i == 1:
            continue  # REMOVED in v2
        if revise and i == 0:
            # MODIFIED in v2: same guid (same name), moved and re-sectioned -- both the placement
            # and the representation change, so this is not a hash coincidence either input covers.
            aa_members.append(_beam(name, 3.0, 0.0 + 0.5, length=5.5, sec="IPE300"))
        else:
            aa_members.append(_beam(name, 3.0, 0.0 + i * 2.0))
    aa_members += _plates("aa", 4, 3.0, 0.0)
    if revise:
        aa_members.append(_beam("aa-bm6", 3.0, 12.0))  # ADDED in v2 -- a name v1 never had
    assembly_aa / aa_members
    storey_a2 / [assembly_aa]

    site_b = _part("SiteB", SpatialTypes.IfcSite)
    storey_b1 = _part("StoreyB1", SpatialTypes.IfcBuildingStorey)
    storey_b1 / (_beams("b1", 6, 0.0, 20.0) + _plates("b1", 4, 0.0, 20.0))
    storey_b2 = _part("StoreyB2", SpatialTypes.IfcBuildingStorey)
    storey_b2 / (_beams("b2", 6, 3.0, 20.0) + _plates("b2", 4, 3.0, 20.0))
    site_b / [storey_b1, storey_b2]

    a / [storey_a1, storey_a2, site_b]
    return a


def build_plant_a_v2_leaf() -> ada.Assembly:
    """A single-product export of ``a1-bm0`` -- UNCHANGED between v1 and v2 -- for the
    leaf-without-stem publish (``--leaf --source``): a caller that has only this one member's
    file, not the whole plant, republishing it against the plant's already-uploaded source blob.

    ``SiteA``'s guid matches the full model's SO ``_hierarchy_revision_for`` (``ada.assets.ifc.
    publish``), which walks THIS file's own ancestor chain and checks it against what is already
    published, finds SiteA published and records a ``hierarchy_revision`` -- the whole point of
    the test this fixture drives. ``StoreyA1Mini`` is a stand-in container (a real IFC beam still
    needs a spatial parent) and is deliberately NOT name-matched to ``StoreyA1``: leaf-without-stem
    must resolve to the nearest PUBLISHED ancestor (SiteA, which has its own manifest from the
    whole-file publish) even when the leaf's own local container is not itself a published node.
    """
    a = ada.Assembly("SiteA", project="plant-a")
    a.guid = _guid("SiteA")
    storey = _part("StoreyA1Mini", SpatialTypes.IfcBuildingStorey)
    storey / [_beam("a1-bm0", 0.0, 0.0)]
    a / [storey]
    return a


def main() -> None:
    build_plant_a(revise=False).to_ifc(destination=OUTPUT_V1, file_obj_only=False, validate=True)
    print(f"wrote {OUTPUT_V1}")
    build_plant_a(revise=True).to_ifc(destination=OUTPUT_V2, file_obj_only=False, validate=True)
    print(f"wrote {OUTPUT_V2}")
    build_plant_a_v2_leaf().to_ifc(destination=OUTPUT_V2_LEAF, file_obj_only=False, validate=True)
    print(f"wrote {OUTPUT_V2_LEAF}")


if __name__ == "__main__":
    main()

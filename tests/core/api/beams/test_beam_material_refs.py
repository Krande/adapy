"""``Beam.material = ...`` keeps ``Material.refs`` in step, as the constructor does.

The setter used to only swap the pointer (the bookkeeping was commented out). ``consolidate_materials`` finds a
material's users through its refs, so two beams given equal-named but distinct materials through the setter were
never repointed at the one material that survives: the IFC writer then raised ``Object Material(Name: "soft" ...)
is not among synced materials``. Found building param_models equipment tents, which set ``.material`` after
constructing each beam.
"""

from __future__ import annotations

import ada


def _beam(name: str, x: float) -> ada.Beam:
    return ada.Beam(name, (x, 0, 0), (x, 0, 1), "IPE300", ada.Material("stiff"))


def test_setter_moves_the_beam_between_refs():
    bm = _beam("bm1", 0)
    stiff = bm.material
    soft = ada.Material("soft")

    bm.material = soft

    assert any(r is bm for r in soft.refs)
    assert not any(r is bm for r in stiff.refs)


def test_setting_the_same_material_twice_does_not_duplicate_the_ref():
    bm = _beam("bm1", 0)
    soft = ada.Material("soft")
    bm.material = soft
    bm.material = soft
    assert sum(r is bm for r in soft.refs) == 1


def test_beams_given_equal_named_materials_by_setter_export_to_ifc(tmp_path):
    """Two parts, each beam re-assigned a fresh "soft" material after construction: consolidation merges the two
    "soft" materials, and every beam must end up on the one that survives."""
    parts = []
    for i in range(2):
        bm = _beam(f"bm{i}", float(i))
        bm.material = ada.Material("soft")
        parts.append(ada.Part(f"p{i}") / bm)
    a = ada.Assembly("a") / parts

    a.to_ifc(tmp_path / "beams.ifc")

    beams = list(a.get_all_physical_objects(by_type=ada.Beam))
    assert {bm.material.name for bm in beams} == {"soft"}
    assert len({id(bm.material) for bm in beams}) == 1


def test_merge_materials_by_properties_repoints_every_ref():
    """It iterated ``mat.refs`` while the setter removed from it, which would skip every other beam."""
    p = ada.Part("p")
    keeper = p.add_material(ada.Material("keeper"))  # first, so it is the one kept
    dup = ada.Material("dup")  # the same properties
    beams = [_beam(f"bm{i}", float(i)) for i in range(4)]
    for bm in beams:
        bm.material = dup
        p.add_beam(bm)

    p.materials.merge_materials_by_properties()

    assert all(bm.material is keeper for bm in beams)
    assert all(any(r is bm for r in keeper.refs) for bm in beams)

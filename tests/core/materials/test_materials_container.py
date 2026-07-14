from ada import Material
from ada.api.containers.materials import Materials


def test_add_material_with_existing_idless_materials_allocates_numeric_id():
    mats = Materials([Material("A"), Material("B")])

    assert len(mats.id_map) == 2
    assert mats.get_by_name("A").id == 1
    assert mats.get_by_name("B").id == 2

    copied_mat = Material("A").copy_to(new_name="A_density_factor")
    added_mat = mats.add(copied_mat)

    assert added_mat is copied_mat
    assert added_mat.id == 3
    assert mats.get_by_id(3) is added_mat

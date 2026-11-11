"""A boolean added after an object's body was built (for a bbox, its cut surfaces, a clash) takes
effect: the cached body is dropped, not served again."""

import ada


def test_a_boolean_added_after_the_body_was_built_cuts_it():
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "BOX200x200x10x10")
    uncut = bm.to_trimesh().volume  # builds and caches the body

    bm.add_boolean(ada.PrimBox("cutter", (0.8, -0.2, -0.2), (1.2, 0.2, 0.2)))

    assert bm.to_trimesh().volume < 0.85 * uncut

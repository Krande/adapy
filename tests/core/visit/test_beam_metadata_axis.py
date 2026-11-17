"""A beam's metadata says where it lies (``axis``): what a viewer needs to tell which end of one member
meets another, and to make the member again from what it shows."""

import ada
from ada.comms.msg_handling.object_metadata import beam_axis, beam_metadata


def test_a_beams_metadata_carries_its_end_points_and_up_direction():
    bm = ada.Beam("bm1", (0, 0, 0), (2, 0, 1), "IPE200", up=(0, 1, 0))
    meta = beam_metadata(bm.name, bm.section, bm.material, beam=bm)
    assert meta["type"] == "Beam" and meta["section"]["name"] == "IPE200"
    assert meta["axis"]["n1"] == [0.0, 0.0, 0.0] and meta["axis"]["n2"] == [2.0, 0.0, 1.0]
    assert meta["axis"]["up"] == [float(v) for v in bm.up]
    assert all(isinstance(v, float) for end in meta["axis"].values() for v in end)


def test_metadata_without_a_beam_is_as_it_was():
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE200")
    assert "axis" not in beam_metadata(bm.name, bm.section, bm.material)
    assert beam_axis(object()) is None


def test_the_design_extension_of_a_part_names_each_beams_axis():
    from ada.visit.scene_handling.scene_from_part import build_design_data_extension

    part = ada.Assembly("a") / (ada.Part("p") / ada.Beam("bm1", (0, 0, 0), (0, 0, 3), "IPE200"))
    ext = build_design_data_extension(part, face_nodes=[])
    meta = ext.model_dump() if hasattr(ext, "model_dump") else ext
    assert _find(meta, "bm1")["axis"]["n2"] == [0.0, 0.0, 3.0]


def _find(obj, name):
    """The metadata entry called ``name``, wherever the extension keeps its object metadata."""
    if isinstance(obj, dict):
        entry = obj.get(name)
        if isinstance(entry, dict) and entry.get("type") == "Beam":
            return entry
        for v in obj.values():
            hit = _find(v, name)
            if hit is not None:
                return hit
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            hit = _find(v, name)
            if hit is not None:
                return hit
    elif hasattr(obj, "__dict__"):
        return _find(vars(obj), name)
    return None

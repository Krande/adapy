"""Per-representation length-unit resolution in the streaming STEP reader.

Mixed-unit files (some CAD systems author sub-assemblies in metres inside an
mm file) must be scaled per representation, else parts in a non-global unit are
mis-sized — e.g. metre-context fasteners in an mm file shrink 1000x and
tessellate to near-zero-area slivers.
"""

from ada.cadit.step.read.stream_reader import (
    _COMPLEX,
    _Enum,
    _Rec,
    _Ref,
    _representation_length_scale,
    _si_unit_length_scale,
    _unit_length_scale,
)


def test_si_unit_length_scale_from_enums():
    assert _si_unit_length_scale([_Enum("MILLI"), _Enum("METRE")]) == 0.001
    assert _si_unit_length_scale([_Enum("CENTI"), _Enum("METRE")]) == 0.01
    assert _si_unit_length_scale([None, _Enum("METRE")]) == 1.0  # $ prefix -> base metre
    assert _si_unit_length_scale([_Enum("MILLI"), _Enum("RADIAN")]) is None  # not a length unit


def test_unit_length_scale_complex_and_plain():
    pool = {
        1: _Rec(_COMPLEX, {"LENGTH_UNIT": [], "SI_UNIT": [_Enum("MILLI"), _Enum("METRE")]}),
        2: _Rec("SI_UNIT", [None, _Enum("METRE")]),
        3: _Rec(_COMPLEX, {"LENGTH_UNIT": [], "CONVERSION_BASED_UNIT": ["INCH"]}),
    }
    assert _unit_length_scale(pool.get, 1) == 0.001
    assert _unit_length_scale(pool.get, 2) == 1.0
    assert _unit_length_scale(pool.get, 3) == 0.0254


def test_representation_length_scale_uses_own_context():
    # rep -> GEOMETRIC_REPRESENTATION_CONTEXT(+GLOBAL_UNIT_ASSIGNED_CONTEXT) -> metre unit
    pool = {
        10: _Rec(_COMPLEX, {"LENGTH_UNIT": [], "SI_UNIT": [None, _Enum("METRE")]}),
        20: _Rec(
            _COMPLEX,
            {
                "GEOMETRIC_REPRESENTATION_CONTEXT": [3],
                "GLOBAL_UNIT_ASSIGNED_CONTEXT": [[_Ref(10)]],
            },
        ),
        # ADVANCED_BREP_SHAPE_REPRESENTATION('', (items), context)
        30: _Rec("ADVANCED_BREP_SHAPE_REPRESENTATION", ["", [_Ref(99)], _Ref(20)]),
    }
    assert _representation_length_scale(pool.get, 30) == 1.0


def test_representation_length_scale_none_when_no_unit():
    pool = {30: _Rec("ADVANCED_BREP_SHAPE_REPRESENTATION", ["", [_Ref(99)]])}
    assert _representation_length_scale(pool.get, 30) is None

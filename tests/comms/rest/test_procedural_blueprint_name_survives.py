"""``blueprint_name`` must survive doc normalization, and must change the cache key.

It is a top-level scalar on the procedural document, set when the user picks an
entry in the viewer's Blueprint dropdown. ``ProceduralDoc`` did not declare it,
and pydantic defaults to ``extra="ignore"`` -- so ``validate_doc`` dropped it
silently, with two compounding consequences:

* the compiler reads ``doc.get("blueprint_name")``, did not find it, and fell
  back to the engine default; and
* ``doc_content_hash`` hashes the normalized doc, so every blueprint hashed the
  SAME and the preview key collided, serving back the previously built GLB.

The visible symptom was a dropdown that changed nothing and raised nothing.
Switching *engine* appeared to work only because ``engine`` is declared.
"""

from ada.comms.rest.procedural import doc_content_hash, validate_doc

_DOC = {
    "spaces": [{"NAME": "c1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3, "FUNCTION": "space", "INCLUDE": True}]
}


def _doc(**over) -> dict:
    return {**_DOC, **over}


def test_blueprint_name_survives_validation():
    out = validate_doc(_doc(blueprint_name="steel_stru"))

    assert out["blueprint_name"] == "steel_stru"


def test_absent_blueprint_name_stays_absent_rather_than_inventing_one():
    # exclude_none drops it entirely; the compiler's own default then applies,
    # which is the pre-existing behaviour for a doc that never named one.
    assert "blueprint_name" not in validate_doc(_doc())


def test_changing_blueprint_changes_the_content_hash():
    a = doc_content_hash(validate_doc(_doc(blueprint_name="steel_stru")))
    b = doc_content_hash(validate_doc(_doc(blueprint_name="none")))
    c = doc_content_hash(validate_doc(_doc(blueprint_name="an-engine-supplied-blueprint")))

    # Three distinct keys. When these collided, the preview endpoint found a
    # cached blob under the first-built key and returned it for every blueprint.
    assert len({a, b, c}) == 3


def test_same_blueprint_still_hashes_stably():
    # The other half of the contract: re-previewing an unchanged doc must stay
    # free, so an identical doc must still produce an identical key.
    first = doc_content_hash(validate_doc(_doc(blueprint_name="steel_stru")))
    second = doc_content_hash(validate_doc(_doc(blueprint_name="steel_stru")))

    assert first == second

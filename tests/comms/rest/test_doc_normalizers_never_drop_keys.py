"""No document normalizer may silently drop a key.

Every ``validate_*_doc`` in the REST layer round-trips a caller-supplied dict
through a pydantic model and returns ``model_dump()`` as the canonical form. That
result is what gets persisted, what the compiler reads with ``doc.get(...)``, and
— for procedural docs — what ``doc_content_hash`` hashes into a preview cache key.

pydantic defaults to ``extra="ignore"``. Under that default, a key the model does
not declare is DELETED on the way through, and the deletion has no failure mode
attached to it:

* the reader's ``doc.get("...")`` returns ``None`` and takes a default, so the
  feature quietly does something else instead of erroring; and
* the content hash is computed over the dict that key is missing from, so two
  documents that differ ONLY in that key produce the SAME cache key. The endpoint
  finds an already-built blob and serves it back. The user sees a control that
  changes nothing, forever, with no error anywhere.

That second effect is why this class survives so long in the wild: it degrades to
a plausible result, not a broken one. ``blueprint_name`` was one instance;
``groups``, ``structures``, ``no_go_walls``, ``detailing_options`` and the engine
manifest's ``export_entrypoint``/``import_entrypoint`` were five more, all found
by the same question.

So this file asserts the invariant rather than the instances: a normalizer may
REJECT a key it does not recognise, or it may PRESERVE it, but it may never
delete it and report success. The validators are discovered by scanning the
modules, so a normalizer added later is covered without anyone remembering to add
it here.
"""

import inspect

import pytest

from ada.comms.rest import catalog as catalog_module
from ada.comms.rest import procedural as procedural_module
from ada.comms.rest.procedural import doc_content_hash, validate_doc

# A minimal VALID document per validator, so the only thing under test is what
# happens to the unrecognised key we add to it.
_MINIMAL_DOC = {
    "validate_doc": {"spaces": [{"NAME": "c1"}]},
    "validate_equipment_doc": {"mass": 500.0},
    "validate_system_doc": {"type": "piping"},
    "validate_engine_doc": {
        "kind": "wheel",
        "repo_url": "https://example.invalid/engine.git",
        "entrypoint": "engine.mod:compile",
    },
}


def _discover_validators():
    """Every public ``validate_*doc`` normalizer in the REST layer."""
    found = {}
    for module in (procedural_module, catalog_module):
        for name, fn in vars(module).items():
            if name.startswith("validate_") and name.endswith("doc") and inspect.isfunction(fn):
                found[name] = fn
    return found


def test_every_normalizer_is_covered_by_this_file():
    """If a new normalizer appears, it must get a minimal doc here — otherwise the
    invariant below would silently skip it, which is the very failure mode this
    file exists to prevent."""
    assert set(_discover_validators()) == set(_MINIMAL_DOC)


@pytest.mark.parametrize("name", sorted(_MINIMAL_DOC))
def test_normalizer_never_silently_drops_an_unrecognised_key(name):
    validator = _discover_validators()[name]
    doc = {**_MINIMAL_DOC[name], "a_key_this_model_does_not_declare": "carry me"}

    try:
        out = validator(doc)
    except ValueError:
        # Rejecting loudly is a fine answer — the caller learns something is
        # wrong. Silence is the thing that isn't.
        return

    assert "a_key_this_model_does_not_declare" in out, (
        f"{name} accepted an unrecognised key and returned success, but the key is "
        f"gone from the result. Either declare it, or reject it — a silent drop "
        f"gives the caller no way to find out."
    )


@pytest.mark.parametrize(
    "key, value",
    [
        # Each of these is read somewhere off a NORMALIZED procedural doc but was
        # not declared on ProceduralDoc, so validate_doc deleted it.
        ("groups", [{"name": "A", "blueprint": "steel_stru"}]),  # viewer cellbuilder
        ("structures", [{"NAME": "S1"}]),  # ProceduralBuilder.from_dict / to_doc
        ("no_go_walls", True),  # ada.topo_model.compile._build_systems
        ("detailing_options", {"a_joint": {"enabled": True}}),  # from_dict fallback
    ],
)
def test_procedural_doc_keys_the_codebase_reads_survive_normalization(key, value):
    out = validate_doc({"spaces": [{"NAME": "c1"}], key: value})

    assert key in out, f"validate_doc dropped {key!r}, which the codebase reads back off the normalized doc"


@pytest.mark.parametrize(
    "key, a, b",
    [
        # The second-order effect, asserted directly: a dropped key does not
        # merely go missing, it COLLIDES the preview cache key, so the endpoint
        # serves back the blob built for the other value.
        ("groups", [{"name": "A", "blueprint": "steel_stru"}], [{"name": "A", "blueprint": "none"}]),
        ("no_go_walls", True, False),
        ("structures", [{"NAME": "S1"}], [{"NAME": "S2"}]),
    ],
)
def test_a_changed_doc_key_changes_the_preview_cache_key(key, a, b):
    base = {"spaces": [{"NAME": "c1"}]}
    first = doc_content_hash(validate_doc({**base, key: a}))
    second = doc_content_hash(validate_doc({**base, key: b}))

    assert (
        first != second
    ), f"two docs differing only in {key!r} hash the same — the preview cache cannot tell them apart"


def test_unchanged_docs_still_hash_the_same():
    """The other half of the contract: re-previewing an unchanged document must
    stay free, so an identical doc must still produce an identical key."""
    doc = {"spaces": [{"NAME": "c1"}], "groups": [{"name": "A", "blueprint": "steel_stru"}]}

    assert doc_content_hash(validate_doc(doc)) == doc_content_hash(validate_doc(doc))


def test_a_doc_that_uses_none_of_the_new_fields_is_byte_identical():
    """The new fields are all None-defaulted and dropped by exclude_none, so an
    existing document's normalized form — and therefore every cache key already
    built from it — does not move."""
    out = validate_doc({"spaces": [{"NAME": "c1"}]})

    assert not {"groups", "structures", "no_go_walls", "detailing_options"} & set(out)


def test_shallow_and_full_normalizers_agree_on_which_keys_survive():
    """The slim API image (no ada importable) falls back to
    ``_validate_doc_shallow``, a SECOND, independently written normalizer. When
    the two disagree about a key, the same request normalizes — and therefore
    hashes — differently depending on which image served it. They have drifted
    before; this pins them together."""
    doc = {
        "spaces": [{"NAME": "c1"}],
        "groups": [{"name": "A", "blueprint": "steel_stru"}],
        "no_go_walls": True,
        "detailing_options": {"a_joint": {"enabled": True}},
        "a_key_neither_model_declares": "carry me",
    }

    full = set(validate_doc(doc))
    shallow = set(procedural_module._validate_doc_shallow(doc))

    assert full == shallow, f"only in full: {sorted(full - shallow)}; only in shallow: {sorted(shallow - full)}"

"""A clash check over a NAMED GROUP of members from several sources (``ada.clash.group`` /
``ada.clash.group_model`` / ``ada.clash.detail``) -- the pure-python half, no REST plumbing.

What is pinned: the request half normalises a group into ONE identity (sorted, deduplicated,
validated) and keys it by its members plus what each source held; the model half finds a joint
that only exists BETWEEN two files, narrows a member to one element by name and path, reports a
member it cannot find instead of failing the group, and rebuilds the same model for a detail job
so the joint ids reproduce.

No fastapi here (see ``test_clash_jobs.py``): the route is covered in
``tests/comms/rest/test_clash_group_routes.py``.
"""

from __future__ import annotations

import contextlib
import pathlib

import pytest

# Run from an un-activated Windows env, numpy finds its BLAS only once adacpp has registered the
# env's DLL directory, and the IFC fixtures below reach `np.dot` before anything else imports it --
# a hard crash (0xc06d007f), not an error. Harmless where adacpp is absent or the env is activated.
with contextlib.suppress(ImportError):
    import adacpp  # noqa: F401

import ada
from ada.api.connections.spec import _clear_registry, get_registered
from ada.clash import ClashOptions
from ada.clash.builtin_specs import register_builtin_specs
from ada.clash.detail import build_detail, joints_by_id
from ada.clash.group import (
    MAX_GROUP_MEMBERS,
    GroupError,
    group_derived_prefix,
    group_token,
    normalise_group,
)
from ada.clash.group_model import GroupModelError, build_group_model, clash_check_group

OPTIONS = ClashOptions(include_plate_joints=False)


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class _Storage:
    """The subset of the worker's sync storage facade the group builder uses."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.fetches: list[str] = []

    def fetch_to_path(self, key: str, dest):
        if key not in self.blobs:
            raise FileNotFoundError(key)
        self.fetches.append(key)
        pathlib.Path(dest).write_bytes(self.blobs[key])
        return dest

    def get_bytes(self, key: str) -> bytes:
        return self.blobs[key]

    def list_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self.blobs if k.startswith(prefix)]


def _ifc_bytes(tmp_path: pathlib.Path, name: str, part: str, beams: list[tuple]) -> bytes:
    a = ada.Assembly(name)
    p = ada.Part(part)
    a.add_part(p)
    for beam_name, n1, n2 in beams:
        p.add_beam(ada.Beam(beam_name, n1, n2, "IPE200"))
    path = tmp_path / f"{name}.ifc"
    a.to_ifc(path)
    return path.read_bytes()


@pytest.fixture
def storage(tmp_path) -> _Storage:
    """Two files, ONE girder each, meeting at (1, 0, 0): neither file alone has a joint.

    ``b.ifc`` also carries a far-away beam in a second part, so a member can be narrowed to an
    element and the narrowing is visible in the counts.
    """
    s = _Storage()
    s.blobs["models/a.ifc"] = _ifc_bytes(tmp_path, "A", "PA", [("ga", (0, 0, 0), (2, 0, 0))])
    a = ada.Assembly("B")
    pb = ada.Part("PB")
    far = ada.Part("Far")
    a.add_part(pb)
    a.add_part(far)
    pb.add_beam(ada.Beam("gb", (1, 0, 0), (1, 2, 0), "IPE200"))
    far.add_beam(ada.Beam("gfar", (50, 50, 0), (52, 50, 0), "IPE200"))
    a.to_ifc(tmp_path / "B.ifc")
    s.blobs["models/b.ifc"] = (tmp_path / "B.ifc").read_bytes()
    return s


def _file(key: str, element: str | None = None, path: list[str] | None = None) -> dict:
    return {"target": {"kind": "file", "source_key": key}, "element": element, "path": path or []}


def _group(*members, name: str = "g") -> dict:
    return normalise_group({"name": name, "members": list(members)})


# ── normalisation ─────────────────────────────────────────────────────


def test_members_are_sorted_and_deduplicated():
    one = _file("b.ifc")
    two = _file("a.ifc", "x", ["P", "x"])
    g = normalise_group({"name": "n", "members": [one, two, dict(one)]})
    assert [m["target"]["source_key"] for m in g["members"]] == ["a.ifc", "b.ifc"]
    assert g == normalise_group({"name": "n", "members": [two, one]})


def test_absent_and_null_mean_the_same_member():
    node = {"kind": "node", "provider": "p", "collection": "c", "subject": "s"}
    spelled = {**node, "revision": None, "node": "s"}
    a = normalise_group({"name": "n", "members": [{"target": node}]})
    b = normalise_group({"name": "n", "members": [{"target": spelled, "element": None, "path": []}]})
    assert a == b
    assert a["members"][0]["target"]["node"] == "s"  # an unnamed node IS its subject


def test_a_whole_source_drops_a_stray_path():
    g = normalise_group({"name": "n", "members": [{"target": {"kind": "file", "source_key": "k"}, "path": ["x"]}]})
    assert g["members"][0]["path"] == []


@pytest.mark.parametrize(
    "raw,needle",
    [
        ({"name": "n", "members": []}, "non-empty"),
        ({"name": "n"}, "non-empty"),
        ({"name": "n", "members": [{"target": {"kind": "folder"}}]}, "kind"),
        ({"name": "n", "members": [{"target": {"kind": "file"}}]}, "source_key"),
        ({"name": "n", "members": [{"target": {"kind": "node", "collection": "c"}}]}, "subject"),
        ({"name": "n", "members": [_file("k", "g1", ["P", "g2"])]}, "does not end"),
        ({"name": "n", "members": [_file("k") | {"path": "P/g1", "element": "g1"}]}, "list"),
        ("not an object", "object"),
    ],
)
def test_a_group_that_cannot_be_checked_is_refused_by_reason(raw, needle):
    with pytest.raises(GroupError, match=needle):
        normalise_group(raw)


def test_a_group_has_a_member_ceiling():
    members = [_file(f"m{i}.ifc") for i in range(MAX_GROUP_MEMBERS + 1)]
    with pytest.raises(GroupError, match=str(MAX_GROUP_MEMBERS)):
        normalise_group({"name": "n", "members": members})
    assert len(normalise_group({"name": "n", "members": members[:-1]})["members"]) == MAX_GROUP_MEMBERS


# ── the key ───────────────────────────────────────────────────────────


def test_the_token_is_stable_and_moves_with_a_source():
    g = _group(_file("a.ifc"), _file("b.ifc"))
    content = {"file:a.ifc": "t1", "file:b.ifc": "t2"}
    token = group_token(g["members"], content)
    assert token == group_token(_group(_file("b.ifc"), _file("a.ifc"))["members"], dict(reversed(content.items())))
    # A re-upload of one member's file moves the key...
    assert token != group_token(g["members"], {**content, "file:b.ifc": "t3"})
    # ...a re-published node too (its token is its resolved revision)...
    assert token != group_token(g["members"], {**content, "node:c/s": "r2"})
    # ...and so does a different member set, while a renamed group does not.
    assert token != group_token(_group(_file("a.ifc"))["members"], content)
    assert token == group_token(_group(_file("a.ifc"), _file("b.ifc"), name="other")["members"], content)
    assert group_derived_prefix(token, "o") == f"_derived/clash/group/{token}/o"


# ── the combined model ────────────────────────────────────────────────


def test_a_joint_between_two_files_is_found_only_when_they_are_checked_together(storage):
    alone = clash_check_group(_group(_file("models/a.ifc")), token="t", storage=storage, options=OPTIONS)
    assert alone["counts"]["joints"] == 0

    doc = clash_check_group(
        _group(_file("models/a.ifc"), _file("models/b.ifc", "gb", ["PB", "gb"])),
        token="t",
        storage=storage,
        options=OPTIONS,
    )
    assert doc["schema"] == "ada.clash/result@2"
    assert doc["source_key"] == "group:t"
    assert doc["counts"]["beams"] == 2  # `gfar` was not selected
    assert doc["counts"]["joints"] == 1
    assert {m["name"] for m in doc["joints"][0]["members"]} == {"ga", "gb"}
    assert {m["target"]["source_key"] for m in doc["provenance"]["group"]["members"]} == {
        "models/a.ifc",
        "models/b.ifc",
    }
    assert doc["provenance"]["members_resolved"] == 2


def test_each_member_is_a_named_part_of_one_assembly(storage):
    built = build_group_model(
        _group(_file("models/a.ifc"), _file("models/b.ifc", "gb", ["PB", "gb"]), name="Deck 3"),
        storage=storage,
    )
    assert built.model.name == "Deck 3"
    # Numbered in the NORMALISED order, so a part's name does not depend on how the caller listed it.
    assert sorted(p.split(": ", 1)[1] for p in built.model.parts) == ["a.ifc", "gb"]
    assert sorted(p.split(": ", 1)[0] for p in built.model.parts) == ["1", "2"]
    assert built.warnings == []


def test_two_members_of_one_file_read_it_once_and_both_resolve(storage):
    built = build_group_model(
        _group(_file("models/b.ifc", "gb", ["PB", "gb"]), _file("models/b.ifc", "gfar", ["Far", "gfar"])),
        storage=storage,
    )
    assert storage.fetches == ["models/b.ifc"]
    names = {b.name for b in built.model.get_all_physical_objects(by_type=ada.Beam)}
    assert names == {"gb", "gfar"}
    assert built.resolved == 2


def test_a_member_inside_another_is_checked_once(storage):
    built = build_group_model(
        _group(_file("models/b.ifc"), _file("models/b.ifc", "gb", ["PB", "gb"])),
        storage=storage,
    )
    assert len(list(built.model.get_all_physical_objects(by_type=ada.Beam))) == 2
    assert any("checked once" in w for w in built.warnings)


def test_a_member_that_cannot_be_found_is_a_warning_and_the_rest_are_checked(storage):
    doc = clash_check_group(
        _group(
            _file("models/a.ifc"),
            _file("models/b.ifc", "gb", ["PB", "gb"]),
            _file("models/b.ifc", "nope", ["PB", "nope"]),
            _file("models/missing.ifc"),
        ),
        token="t",
        storage=storage,
        options=OPTIONS,
    )
    assert doc["counts"]["joints"] == 1
    text = " | ".join(doc["warnings"])
    assert "'nope'" in text
    assert "models/missing.ifc" in text


def test_a_group_where_nothing_resolves_fails(storage):
    with pytest.raises(GroupModelError, match="none of the 2"):
        build_group_model(
            _group(_file("models/missing.ifc"), _file("models/b.ifc", "nope", ["nope"])),
            storage=storage,
        )


def test_a_node_member_is_read_through_its_provider_and_joins_a_file(storage):
    """A published node next to a file: the joint between them is found the same way."""
    seen = []

    def read_node(*, collection, subject, storage, revision, node):
        seen.append((collection, subject, revision, node))
        part = ada.Part(node)
        part.add_beam(ada.Beam("gn", (1, 0, 0), (1, -2, 0), "IPE200"))
        return part

    node = {"kind": "node", "provider": "p", "collection": "c", "subject": "s", "revision": "r1"}
    doc = clash_check_group(
        _group(_file("models/a.ifc"), {"target": node}),
        token="t",
        storage=storage,
        options=OPTIONS,
        read_node=read_node,
    )
    assert seen == [("c", "s", "r1", "s")]
    assert doc["counts"]["joints"] == 1
    assert {m["name"] for m in doc["joints"][0]["members"]} == {"ga", "gn"}


# ── detail ───────────────────────────────────────────────────────────


def test_a_rebuilt_group_model_reproduces_the_joint_ids_and_details(storage):
    group = _group(_file("models/a.ifc"), _file("models/b.ifc", "gb", ["PB", "gb"]))
    doc = clash_check_group(group, token="t", storage=storage, options=OPTIONS)
    ids = [j["id"] for j in doc["joints"]]
    assert ids

    # What a detail job does: rebuild from the result's own provenance, re-identify, build.
    rebuilt = build_group_model(doc["provenance"]["group"], storage=storage).model
    by_id = joints_by_id(rebuilt, ClashOptions.from_dict(doc["options"]))
    assert set(ids) <= set(by_id)

    register_builtin_specs()
    glb, stats = build_detail(
        get_registered("builtin.girder_gusset"),
        spec_name="builtin.girder_gusset",
        joint_ids=ids,
        by_id=by_id,
        gen_options={},
    )
    assert glb
    assert stats["joints"]["count"] == 1

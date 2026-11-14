"""Simulation subjects (``ada.simulation.subjects``): the kinds, the weld-assembly file made of a
built part, how it is parsed, which providers accept it and how a run is handed it."""

from __future__ import annotations

import gzip
import json
import threading

import numpy as np
import pytest

import ada
from ada import plugins
from ada.api.curves import CurveOpen3d
from ada.simulation import (
    SUBJECT_KINDS,
    ProviderDescription,
    SubjectRef,
    register_simulation_provider,
    simulation_provider_specs,
    subject_kind,
    validate_parameters,
)
from ada.simulation.runner import execute_run
from ada.simulation.runs import new_run_id, new_run_record
from ada.simulation.subjects import (
    WELD_ASSEMBLY_SCHEMA,
    WELD_ASSEMBLY_SCHEMA_CURVED,
    WeldAssemblyError,
    crop_prism,
    dump_weld_assembly,
    parse_weld_assembly,
    weld_assembly_from_part,
    weld_seam,
    write_weld_assembly,
)
from ada.simulation.testing import FakeSimulationProvider

H = 0.04  # half of an 80 mm box


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


def _beams():
    """A 1 m landing along X (its top face at z = 40 mm; the part frame lifts it onto the floor) and an incoming 80 mm box standing on it."""
    landing = ada.Beam("landing", (-0.5, 0, 0), (0.5, 0, 0), "BOX80x80x4x4")
    incoming = ada.Beam("incoming", (0, 0, H), (0, 0, 1.0), "BOX80x80x4x4")
    return landing, incoming


def _welds(incoming, landing):
    """Four fillets round the incoming's end face, on the landing's top face."""
    corners = [(-H, -H, H), (H, -H, H), (H, H, H), (-H, H, H)]
    out = []
    for i in range(4):
        p1, p2 = corners[i], corners[(i + 1) % 4]
        outward = (np.array(p1) + np.array(p2)) / 2 - np.array([0, 0, H])
        profile_normal = np.cross([0, 0, -1], outward / np.linalg.norm(outward))
        out.append(
            ada.Weld(
                f"w{i}",
                weld_type="FILLET",
                members=(incoming, landing),
                profile=[(0, 0), (-0.003, 0), (0, 0.003)],
                xdir=ada.Direction(0, 0, -1),
                sweep_curve=CurveOpen3d([p1, p2]),
                profile_normal=ada.Direction(*profile_normal),
                throat=0.0028,
            )
        )
    return out


@pytest.fixture(scope="module")
def connection():
    landing, incoming = _beams()
    conn = ada.Connection("sample", spec_name="sample.box_to_box")
    conn.add_beam(landing)
    conn.add_beam(incoming)
    for weld in _welds(incoming, landing):
        conn.add_weld(weld)
    return conn


@pytest.fixture(scope="module")
def assembly(connection) -> dict:
    return weld_assembly_from_part(connection, stub_length=0.12, source={"kind": "connection-spec", "spec_name": "x"})


# ---- the kinds ------------------------------------------------------------------------------------


def test_the_one_subject_kind_is_the_weld_assembly():
    assert list(SUBJECT_KINDS) == ["weld-assembly"]
    kind = subject_kind("weld-assembly")
    assert kind.media_type == "application/json" and WELD_ASSEMBLY_SCHEMA in kind.description
    assert kind.to_dict()["id"] == "weld-assembly"
    with pytest.raises(ValueError, match=r"unknown subject kind 'mesh' \(known: weld-assembly\)"):
        subject_kind("mesh")


def test_a_subject_reference_reads_back_from_a_record_and_ignores_what_is_not_one():
    ref = SubjectRef("weld-assembly", "a/b.json")
    assert SubjectRef.from_dict(ref.to_dict()) == ref
    for bad in (None, "x", {}, {"kind": "weld-assembly"}, {"kind": 1, "key": "k"}):
        assert SubjectRef.from_dict(bad) is None


# ---- a built part as a weld assembly --------------------------------------------------------------


def test_the_assembly_has_every_weld_with_its_normals(assembly):
    # Every seam straight: the document is a /1 one, as it always was, without the per-point fields.
    assert assembly["schema"] == "ada-weld-assembly/1" and assembly["units"] == "m"
    assert not any("tangents" in w or "closed" in w for w in assembly["welds"])
    assert assembly["source"] == {"kind": "connection-spec", "spec_name": "x"}
    assert [w["id"] for w in assembly["welds"]] == ["weld1", "weld2", "weld3", "weld4"]
    for weld in assembly["welds"]:
        assert weld["type"] == "fillet" and weld["throat"] == pytest.approx(0.0028)
        assert weld["members"] == ["incoming", "landing"]
        points = np.array(weld["points"])
        assert np.linalg.norm(points[-1] - points[0]) == pytest.approx(0.08)
        n_a, n_b = np.array(weld["normal_a"]), np.array(weld["normal_b"])
        tangent = (points[-1] - points[0]) / np.linalg.norm(points[-1] - points[0])
        assert np.allclose(n_a, [0, 0, 1]) and np.linalg.norm(n_b) == pytest.approx(1.0)
        assert abs(n_b @ tangent) < 1e-6 and abs(n_b @ n_a) < 1e-6
        middle = points.mean(axis=0)
        assert n_b @ [middle[0], middle[1], 0.0] > 0  # away from the incoming's axis
    # In the part frame: on the landing's top face (z = its depth), centred, 80 mm square.
    corners = np.array([p for w in assembly["welds"] for p in w["points"]])
    assert set(np.round(corners[:, 2], 6)) == {0.08}
    assert corners[:, :2].min() == pytest.approx(-H) and corners[:, :2].max() == pytest.approx(H)


def test_the_members_stand_in_the_part_frame_and_are_cut_to_stubs_on_request(assembly):
    by_name = {m["name"]: np.array(m["vertices"]) for m in assembly["members"]}
    landing, incoming = by_name["landing"], by_name["incoming"]
    assert (landing.min(axis=0), landing.max(axis=0)) == (
        pytest.approx([-0.16, -0.04, 0.0]),
        pytest.approx([0.16, 0.04, 0.08]),
    )
    assert (incoming.min(axis=0), incoming.max(axis=0)) == (
        pytest.approx([-0.04, -0.04, 0.08]),
        pytest.approx([0.04, 0.04, 0.20]),
    )
    assert assembly["stub_length"] == 0.12 and assembly["landing"] == "landing"
    for member in assembly["members"]:
        faces = np.array(member["faces"])
        assert faces.min() >= 0 and faces.max() < len(member["vertices"])


def test_the_member_meshes_face_outward_and_the_mass_is_steels(assembly):
    area = 0.08**2 - 0.072**2
    by_name = {m["name"]: m for m in assembly["members"]}
    assert by_name["landing"]["mass_kg"] == pytest.approx(7850 * area * 0.32, rel=1e-3)
    assert by_name["incoming"]["mass_kg"] == pytest.approx(7850 * area * 0.12, rel=1e-3)
    assert assembly["mass_kg"] == pytest.approx(sum(m["mass_kg"] for m in assembly["members"]), abs=1e-3)
    assert assembly["cog"][:2] == pytest.approx([0.0, 0.0], abs=1e-6) and 0.04 < assembly["cog"][2] < 0.14


def test_without_a_stub_length_the_members_stay_whole(connection):
    whole = weld_assembly_from_part(connection)
    assert "stub_length" not in whole and "source" not in whole
    by_name = {m["name"]: np.array(m["vertices"]) for m in whole["members"]}
    assert np.ptp(by_name["landing"][:, 0]) == pytest.approx(1.0)
    assert np.ptp(by_name["incoming"][:, 2]) == pytest.approx(0.96)


def test_members_outside_the_connection_are_taken_when_given_and_by_name_otherwise():
    """A detailing engine's connection holds welds that name members living in another part."""
    landing, incoming = _beams()
    beams = ada.Part("beams")
    beams.add_beam(landing)
    beams.add_beam(incoming)
    conn = ada.Connection("detail", spec_name="detailing")
    for weld in _welds(incoming, landing):
        conn.add_weld(weld)
    assert not list(conn.get_all_physical_objects())

    # Named by the welds: both members are found without being told.
    named = weld_assembly_from_part(conn, stub_length=0.12)
    assert {m["name"] for m in named["members"]} == {"incoming", "landing"}

    # Told: exactly those, in that order, and a member no weld names may be one of them.
    extra = ada.Beam("extra", (0.3, 0.2, 0), (0.3, 0.6, 0), "BOX80x80x4x4")
    given = weld_assembly_from_part(conn, members=[landing, incoming, extra], stub_length=0.12)
    assert [m["name"] for m in given["members"]] == ["landing", "incoming", "extra"]
    only = weld_assembly_from_part(conn, members=[incoming])
    assert [m["name"] for m in only["members"]] == ["incoming"]
    assert len(given["welds"]) == 4


def test_a_part_that_cannot_be_an_assembly_is_refused_with_the_reason():
    with pytest.raises(WeldAssemblyError, match="no welds"):
        weld_assembly_from_part(ada.Connection("empty", spec_name="x"))
    landing, incoming = _beams()
    conn = ada.Connection("c", spec_name="x")
    for weld in _welds(incoming, landing):
        conn.add_weld(weld)
    with pytest.raises(WeldAssemblyError, match="no members"):
        weld_assembly_from_part(conn, members=[])


def test_crop_prism_slides_end_caps_and_leaves_a_shaped_end_alone():
    bar = np.array([[x, y, z] for x in (0.0, 1.0) for y in (0.0, 0.1) for z in (0.0, 0.1)])
    cut = crop_prism(bar, (0, 0, 0), (1, 0, 0), 0.2, 0.6)
    assert sorted(set(cut[:, 0])) == [0.2, 0.6] and np.allclose(cut[:, 1:], bar[:, 1:])
    mitred = bar.copy()
    mitred[mitred[:, 0] == 1.0, 0] = [0.9, 0.9, 1.0, 1.0]
    assert np.allclose(crop_prism(mitred, (0, 0, 0), (1, 0, 0), -1.0, 0.6), mitred)


# ---- parse and validate ---------------------------------------------------------------------------


def test_an_assembly_round_trips_through_its_file_plain_or_gzipped(assembly):
    raw = dump_weld_assembly(assembly)
    assert parse_weld_assembly(raw) == assembly
    assert parse_weld_assembly(gzip.compress(raw)) == assembly
    assert parse_weld_assembly(raw.decode("utf-8")) == assembly


@pytest.mark.parametrize(
    "change,needle",
    [
        (lambda d: d.update(schema="weld-job/2"), "not a ada-weld-assembly/1 or ada-weld-assembly/2 document"),
        (lambda d: d.update(units="mm"), "units must be 'm'"),
        (lambda d: d.update(welds=[]), "no welds"),
        (lambda d: d.update(members=[]), "no members"),
        (lambda d: d["welds"][0].update(points=[[0, 0, 0]]), "at least 2"),
        (lambda d: d["welds"][0].update(normal_a=[0, 1]), "normal_a must be"),
        (lambda d: d["welds"][1].update(id=d["welds"][0]["id"]), "share an id"),
        (lambda d: d["members"][0].update(faces=[[0, 1, 10**7]]), "names a vertex it does not have"),
        (lambda d: d["members"][0].pop("name"), "has no name"),
    ],
)
def test_a_file_that_is_not_a_weld_assembly_is_refused_with_the_reason(assembly, change, needle):
    broken = json.loads(json.dumps(assembly))
    change(broken)
    with pytest.raises(WeldAssemblyError, match=needle):
        parse_weld_assembly(json.dumps(broken).encode())


def test_unreadable_bytes_are_refused():
    for raw in (b"not json", b"[]", b"\x1f\x8bnot gzip"):
        with pytest.raises(WeldAssemblyError):
            parse_weld_assembly(raw)


def test_writing_stores_it_gzipped_at_rest_in_either_kind_of_storage(assembly):
    class Scoped:
        def __init__(self):
            self.put = []

        def put_bytes(self, key, data, content_encoding=None):
            self.put.append((key, data, content_encoding))

    bound = Scoped()
    write_weld_assembly(bound, None, "_dev/a.weld-assembly.json", assembly)
    assert bound.put[0][0] == "_dev/a.weld-assembly.json" and bound.put[0][2] == "gzip"
    assert parse_weld_assembly(bound.put[0][1]) == assembly

    class Aware:
        def put_bytes(self, scope, key, data, content_encoding=None):
            return (scope, key, content_encoding, data)

    scope, key, encoding, data = write_weld_assembly(Aware(), "user:me", "k.json", assembly)
    assert (scope, key, encoding) == ("user:me", "k.json", "gzip") and parse_weld_assembly(data) == assembly
    with pytest.raises(WeldAssemblyError):
        write_weld_assembly(bound, None, "k.json", {"schema": "nope"})


# ---- a provider accepts kinds; a run is handed its subject ---------------------------------------


def test_a_provider_describes_the_kinds_it_accepts():
    assert ProviderDescription("x").accepts == () and ProviderDescription("x").to_dict()["accepts"] == []
    described = ProviderDescription("x", accepts=("weld-assembly",)).to_dict()
    assert described["accepts"] == ["weld-assembly"]
    with pytest.raises(ValueError, match="unknown subject kind 'mesh'"):
        ProviderDescription("x", accepts=("mesh",))

    register_simulation_provider(FakeSimulationProvider("takes", accepts=("weld-assembly",)))
    register_simulation_provider(FakeSimulationProvider("plain"))
    specs = {s["id"]: s for s in simulation_provider_specs()}
    assert specs["takes"]["accepts"] == ["weld-assembly"] and specs["plain"]["accepts"] == []


class _Storage:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.lock = threading.Lock()

    def get_bytes(self, key):
        with self.lock:
            if key not in self.blobs:
                raise FileNotFoundError(key)
            return self.blobs[key]

    def put_bytes(self, key, data, content_encoding=None):
        with self.lock:
            self.blobs[key] = bytes(data)

    def put_path(self, key, src):
        with open(src, "rb") as fh:
            self.put_bytes(key, fh.read())

    def fetch_to_path(self, key, dest):
        with open(dest, "wb") as fh:
            fh.write(self.get_bytes(key))
        return dest


def test_the_run_record_keeps_the_subject_and_the_case_is_handed_it():
    seen = {}

    class Reading(FakeSimulationProvider):
        def run(self, case, ctx):
            seen["subject"] = case.subject
            seen["bytes"] = ctx.read_input(case.subject.key) if case.subject else None
            return super().run(case, ctx)

    provider = Reading("takes", accepts=("weld-assembly",))
    spec = {"id": provider.id, "domain": provider.domain, "engine": provider.engine}
    storage = _Storage()
    storage.blobs["a.json"] = b"{}"
    params = validate_parameters(provider.describe().parameters_schema, {"steps": 1})

    plain = new_run_record(run_id=new_run_id(), provider=spec, parameters=params, inputs=[], started_by={})
    assert plain["subject"] is None

    record = new_run_record(
        run_id=new_run_id(),
        provider=spec,
        parameters=params,
        inputs=["a.json"],
        started_by={},
        subject={"kind": "weld-assembly", "key": "a.json"},
    )
    assert record["subject"] == {"kind": "weld-assembly", "key": "a.json"}
    done = execute_run(provider, record, storage, scope="user:me")
    assert done["status"] == "done", done["error"]
    assert seen["subject"] == SubjectRef("weld-assembly", "a.json") and seen["bytes"] == b"{}"
    assert done["summary"]["subject"] == {"kind": "weld-assembly", "key": "a.json"}

    execute_run(provider, plain, storage, scope="user:me")
    assert seen["subject"] is None


# ---- curved and closed seams: a tubular joint's saddle ---------------------------------------------

R_CHORD, R_BRACE = 0.1, 0.05  # TUB100x8 and TUB50x5: the section names give the outer radius in mm


def _saddle_points(n: int = 48) -> list:
    """Where a brace (radius R_BRACE, axis +Z through the origin) meets a chord (radius R_CHORD, axis X):
    round the brace, at the height of the chord's surface. Closed: the first point repeats at the end."""
    out = []
    for k in range(n + 1):
        a = 2 * np.pi * k / n
        x, y = R_BRACE * np.cos(a), R_BRACE * np.sin(a)
        out.append((float(x), float(y), float(np.sqrt(R_CHORD**2 - y**2))))
    return out


def _saddle_part(*, with_box: bool = False):
    chord = ada.Beam("chord", (-0.4, 0, 0), (0.4, 0, 0), "TUB100x8")
    brace = ada.Beam("brace", (0, 0, 0), (0, 0, 0.5), "TUB50x5")
    conn = ada.Connection("tubular", spec_name="sample.tubular")
    conn.add_beam(chord)
    conn.add_beam(brace)
    conn.add_weld(
        ada.Weld(
            "saddle",
            weld_type="FILLET",
            members=(brace, chord),
            profile=[(0, 0), (-0.003, 0), (0, 0.003)],
            xdir=ada.Direction(0, 0, -1),
            sweep_curve=CurveOpen3d(_saddle_points()),
            throat=0.003,
        )
    )
    if with_box:  # a straight seam besides it: a stub on the chord's far end
        landing, incoming = _beams()
        conn.add_beam(landing)
        conn.add_beam(incoming)
        conn.add_weld(_welds(incoming, landing)[0])
    return conn


@pytest.fixture(scope="module")
def saddle() -> dict:
    return weld_assembly_from_part(_saddle_part(), stub_length=None)


def test_a_saddle_seam_is_a_closed_line_with_its_frames_point_by_point(saddle):
    assert saddle["schema"] == WELD_ASSEMBLY_SCHEMA_CURVED == "ada-weld-assembly/2"
    (weld,) = saddle["welds"]
    assert weld["closed"] is True
    points = np.array(weld["points"])
    assert len(points) == 48  # the first point is not repeated at the end
    tangents, n_a, n_b = (np.array(weld[k]) for k in ("tangents", "normals_a", "normals_b"))
    assert tangents.shape == n_a.shape == n_b.shape == points.shape
    for v in (tangents, n_a, n_b):
        assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
    # The direction of travel follows the line, round the loop and across its start: no zero, no flip.
    steps = np.roll(points, -1, axis=0) - points
    assert np.all(np.einsum("ij,ij->i", tangents, steps) > 0)
    assert np.all(np.einsum("ij,ij->i", tangents, np.roll(tangents, -1, axis=0)) > np.cos(np.radians(15)))
    # Part frame: the brace stands up (+Z), the chord along +X. The face the seam lies on is the chord's
    # surface, whose normal leans out from its axis; the other is the brace's side, square to its axis.
    centre = points.mean(axis=0)
    chord_axis_z = points[:, 2].max() - R_CHORD
    radial_chord = points - np.array([0.0, 0.0, chord_axis_z]) * [0, 0, 1] - points * [1, 0, 0]
    radial_chord = radial_chord / np.linalg.norm(radial_chord, axis=1)[:, None]
    assert np.all(np.einsum("ij,ij->i", n_a, radial_chord) > np.cos(np.radians(6)))
    radial_brace = (points - centre) * [1, 1, 0]
    radial_brace = radial_brace / np.linalg.norm(radial_brace, axis=1)[:, None]
    assert np.all(np.einsum("ij,ij->i", n_b, radial_brace) > np.cos(np.radians(6)))
    assert np.allclose(np.einsum("ij,ij->i", tangents, n_a), 0.0, atol=1e-5)
    # The one normal pair a /1 reader would take is the middle point's.
    assert weld["normal_a"] == weld["normals_a"][24] and weld["normal_b"] == weld["normals_b"][24]
    assert parse_weld_assembly(dump_weld_assembly(saddle)) == saddle
    assert weld_seam(weld)["closed"] is True and weld_seam(weld)["tangents"] == weld["tangents"]


def test_with_a_curved_seam_a_straight_one_is_written_point_by_point_too():
    doc = weld_assembly_from_part(_saddle_part(with_box=True), stub_length=None)
    assert doc["schema"] == WELD_ASSEMBLY_SCHEMA_CURVED
    straight = doc["welds"][1]
    assert straight["closed"] is False and len(straight["points"]) == 2
    p = np.array(straight["points"])
    t = (p[1] - p[0]) / np.linalg.norm(p[1] - p[0])
    assert np.allclose(straight["tangents"], [t, t], atol=1e-6)
    assert straight["normals_a"] == [straight["normal_a"]] * 2 and straight["normals_b"] == [straight["normal_b"]] * 2


def test_a_straight_weld_of_a_v1_document_is_the_two_point_case_of_a_seam(assembly):
    weld = assembly["welds"][0]
    seam = weld_seam(weld)
    p = np.array(weld["points"])
    assert seam["closed"] is False and seam["points"] == weld["points"]
    assert np.allclose(seam["tangents"], [(p[-1] - p[0]) / np.linalg.norm(p[-1] - p[0])] * len(p))
    assert seam["normals_a"] == [weld["normal_a"]] * len(p) and seam["normals_b"] == [weld["normal_b"]] * len(p)


@pytest.mark.parametrize(
    "change,needle",
    [
        (lambda w: w.pop("closed"), "closed must be true or false"),
        (lambda w: w.pop("tangents"), "tangents must be one"),
        (lambda w: w.update(normals_a=w["normals_a"][:-1]), "normals_a must be one"),
        (lambda w: w["normals_b"].__setitem__(0, [0.0, 0.0, 2.0]), "normals_b must be unit"),
        (lambda w: w["points"].__setitem__(1, list(w["points"][0])), "zero-length step"),
        (
            lambda w: (
                w["points"].append(list(w["points"][0]))
                or w["tangents"].append(w["tangents"][0])
                or w["normals_a"].append(w["normals_a"][0])
                or w["normals_b"].append(w["normals_b"][0])
            ),
            "zero-length step",
        ),
        (
            lambda w: w.update(
                points=w["points"][:2],
                tangents=w["tangents"][:2],
                normals_a=w["normals_a"][:2],
                normals_b=w["normals_b"][:2],
            ),
            "closed and must have at least 3",
        ),
    ],
)
def test_a_curved_seam_without_its_frames_is_refused_with_the_reason(saddle, change, needle):
    broken = json.loads(json.dumps(saddle))
    change(broken["welds"][0])
    with pytest.raises(WeldAssemblyError, match=needle):
        parse_weld_assembly(json.dumps(broken).encode())


def test_an_arc_of_a_sweep_curve_is_sampled_along_the_arc():
    # A corner of a seam rounded off (a radius at the middle point: line, arc, line): its line is the arc
    # in steps of at most SEAM_SPACING, not the corner of its control points.
    from ada.simulation.subjects import SEAM_SPACING

    chord = ada.Beam("chord", (-0.4, 0, 0), (0.4, 0, 0), "BOX80x80x4x4")
    brace = ada.Beam("brace", (0, 0, H), (0, 0, 0.5), "TUB50x5")
    conn = ada.Connection("arc", spec_name="x")
    conn.add_beam(chord)
    conn.add_beam(brace)
    conn.add_weld(
        ada.Weld(
            "corner",
            weld_type="FILLET",
            members=(brace, chord),
            profile=[(0, 0), (-0.003, 0), (0, 0.003)],
            xdir=ada.Direction(0, 0, -1),
            sweep_curve=CurveOpen3d([(0.05, -0.05, H), (0.05, 0.05, H, 0.03), (-0.05, 0.05, H)]),
            throat=0.003,
        )
    )
    doc = weld_assembly_from_part(conn, stub_length=None)
    (weld,) = doc["welds"]
    p = np.array(weld["points"])
    assert doc["schema"] == WELD_ASSEMBLY_SCHEMA_CURVED and weld["closed"] is False
    steps = np.linalg.norm(np.diff(p, axis=0), axis=1)
    assert len(p) > 4 and np.all(steps[1:-1] <= SEAM_SPACING + 1e-9)
    # The corner is cut: no point is within the radius' worth of it.
    corner = p[np.argmax(p[:, 0] + p[:, 1])]
    assert np.linalg.norm(p - corner, axis=1).max() > 0.05
    assert np.all(np.linalg.norm(p[1:-1] - p[1:-1].mean(axis=0), axis=1) < 0.03)
    tangents = np.array(weld["tangents"])
    assert np.all(np.einsum("ij,ij->i", tangents[:-1], tangents[1:]) > 0.5)

import xml.etree.ElementTree as ET

import numpy as np
import pytest

import ada
from ada.api.beams.justification import Justification
from ada.api.spatial.eq_types import EquipRepr


def _poly_area(outline) -> float:
    p = np.asarray(outline, dtype=float)
    n = np.zeros(3)
    for i in range(len(p)):
        n += np.cross(p[i], p[(i + 1) % len(p)])
    return 0.5 * float(np.linalg.norm(n))


def test_roundtrip_xml(fem_files, tmp_path):
    original_xml_file = fem_files / "sesam/xml_all_basic_props.xml"
    new_xml = tmp_path / "basic_props.xml"

    a = ada.from_genie_xml(original_xml_file)

    global_constraints = a.concept_fem.constraints.get_global_constraint_concepts()
    assert len(global_constraints.point_constraints) == 3

    global_loads = a.concept_fem.loads.get_global_load_concepts()
    assert len(global_loads.load_cases) == 0

    a.to_genie_xml(new_xml)


def test_create_sesam_xml_from_mixed(mixed_model, tmp_path):
    xml_file = tmp_path / "mixed_xml_model.xml"

    mixed_model.to_genie_xml(xml_file)


def test_streaming_xml_byte_identical_to_dom(tmp_path):
    # The streaming writer must produce byte-identical output to the DOM writer;
    # it only changes the assembly strategy (per-object flush vs whole-tree),
    # not the geometry. Cover beams + a (mergeable) plate pair. ``to_genie_xml``
    # consolidates sections/materials in place, so build a fresh model per
    # writer rather than writing the same object twice.
    def build():
        p = ada.Part("P") / (
            ada.Beam("bm1", (0, 0, 0), (10, 0, 0), "IPE300"),
            ada.Plate("pl1", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.2),
            ada.Plate("pl2", [(10, 0), (20, 0), (20, 10), (10, 10)], 0.2),
        )
        return ada.Assembly("a") / p

    dom = tmp_path / "dom.xml"
    stream = tmp_path / "stream.xml"
    build().to_genie_xml(dom, streaming=False)
    build().to_genie_xml(stream, streaming=True)

    assert dom.read_bytes() == stream.read_bytes()


def test_streaming_xml_face_source_object_free(fem_files, tmp_path):
    # merge_strategy sources plates from the vectorized object-free FEM-shell
    # face engine: no Plate objects are materialised, and the output is
    # geometrically equivalent (same covered area) to the object merge.
    from ada.cadit.gxml.store import GxmlStore

    ref = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    ref.create_objects_from_fem(merge=True)
    ref_area = 0.0
    for pl in ref.get_all_physical_objects(by_type=ada.Plate):
        ap = pl.placement.get_absolute_placement(include_rotations=True)
        ident = ada.Placement()
        glob = [
            ap.transform_array_from_other_place(np.asarray([pt], dtype=float), ident, ignore_translation=False)[0]
            for pt in pl.poly.points3d
        ]
        ref_area += _poly_area(glob)

    a = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    a.create_objects_from_fem(skip_plates=True, merge=True)  # beams only
    dest = tmp_path / "faces.xml"
    a.to_genie_xml(dest, streaming=True, merge_strategy="coplanar")

    # object-free: the part never materialised any Plate
    assert len(list(a.get_all_physical_objects(by_type=ada.Plate))) == 0

    plates = list(GxmlStore(dest).iter_plates_from_xml())
    assert len(plates) >= 1
    assert sum(_poly_area(p.nodes) for p in plates) == pytest.approx(ref_area, rel=1e-4)


def test_streaming_xml_from_fem_byte_identical(fem_files, tmp_path):
    # FEM-source path: load a Sesam mesh, rebuild + merge concept objects, then
    # confirm the streaming writer matches the DOM writer byte-for-byte on the
    # merged model (the case the streaming writer exists for). Fresh load per
    # writer — to_genie_xml consolidates in place.
    def build():
        a = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
        a.create_objects_from_fem(merge=True)
        return a

    dom = tmp_path / "dom.xml"
    stream = tmp_path / "stream.xml"
    build().to_genie_xml(dom, streaming=False)
    build().to_genie_xml(stream, streaming=True)

    assert dom.read_bytes() == stream.read_bytes()


class TestEmbeddedSat:
    """Genie stores the ACIS body under structure_domain/geometry as a
    sat_embedded_sequence: zipped to one b64temp.sat member, the compressed
    bytes cut into segments, each segment base64'd on its own inside CDATA.
    Shapes asserted here were read off a Genie-authored export."""

    @staticmethod
    def _model():
        deck = ada.Plate.from_3d_points("deck", [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], 0.01)
        bulk = ada.Plate.from_3d_points("bulk", [(0, 5, 0), (10, 5, 0), (10, 5, 5), (0, 5, 5)], 0.01)
        return ada.Assembly("a") / (ada.Part("p") / [deck, bulk])

    def test_embed_sat_is_the_default(self, tmp_path):
        """Without SAT, Genie rebuilds every plate's ACIS on import — the thing
        that made a large model take minutes to open."""
        out = self._model().to_genie_xml(tmp_path / "plate.xml")
        raw = out.read_text()
        assert "<sat_embedded_sequence" in raw
        assert "<polygons>" not in raw

    def test_geometry_is_the_last_child_of_structure_domain(self, tmp_path):
        out = self._model().to_genie_xml(tmp_path / "plate.xml")
        sd = ET.parse(str(out)).getroot().find("./model/structure_domain")
        assert [c.tag for c in sd][-1] == "geometry"

    def test_sequence_attributes_and_cdata(self, tmp_path):
        out = self._model().to_genie_xml(tmp_path / "plate.xml")
        seq = ET.parse(str(out)).getroot().find(".//geometry/sat_embedded_sequence")
        assert seq.attrib == {"encoding": "base64", "compression": "zip", "tag_name": "dnvscp"}
        assert len(seq.findall("cdata_segment")) >= 1
        raw = out.read_text()
        assert "<cdata_segment><![CDATA[" in raw
        assert "__ADA_CDATA_SEGMENT" not in raw  # every placeholder spliced

    def test_sat_reads_back_out_of_the_xml(self, tmp_path):
        from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml

        out = self._model().to_genie_xml(tmp_path / "plate.xml")
        sat = get_sat_text_from_xml(out)
        assert sat.startswith("2000 0 1 0")
        assert sat.rstrip().endswith("End-of-ACIS-data")

    def test_plate_references_every_face_it_was_split_into(self, tmp_path):
        """The bulkhead cuts the deck in two, so the deck's sheet must name both
        halves — Genie writes up to 10 faces for one plate on a real model."""
        out = self._model().to_genie_xml(tmp_path / "plate.xml")
        refs = {
            fp.get("name"): [f.get("face_ref") for f in fp.iterfind(".//sat_reference/face")]
            for fp in ET.parse(str(out)).getroot().iterfind(".//structure/flat_plate")
        }
        assert len(refs["deck"]) == 2
        assert len(refs["bulk"]) == 1
        assert not set(refs["deck"]) & set(refs["bulk"])  # no face claimed twice

    def test_segments_split_the_zipped_body_at_1mib(self):
        """Each segment is base64 of its own 1 MiB slice of the *compressed*
        stream, so a reader must join the decoded bytes, not the base64 text."""
        import base64

        from ada.cadit.gxml.write.write_sat_embedded import (
            SEGMENT_BYTES,
            sat_to_base64_segments,
        )

        segments = sat_to_base64_segments("x" * (6 * SEGMENT_BYTES))  # compresses small
        assert len(segments) >= 1
        decoded = [base64.b64decode(s) for s in segments]
        assert all(len(d) == SEGMENT_BYTES for d in decoded[:-1])
        assert 0 < len(decoded[-1]) <= SEGMENT_BYTES

    def test_multi_segment_body_round_trips(self, tmp_path):
        """A body larger than one segment must reassemble exactly."""
        import base64
        import io
        import zipfile

        from ada.cadit.gxml.write.write_sat_embedded import (
            SEGMENT_BYTES,
            ZIP_MEMBER,
            sat_to_base64_segments,
        )

        # incompressible payload, so the zip is comfortably over one segment
        body = base64.b64encode(np.random.default_rng(0).bytes(3 * SEGMENT_BYTES)).decode()
        segments = sat_to_base64_segments(body)
        assert len(segments) > 1

        blob = b"".join(base64.b64decode(s) for s in segments)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            assert zf.namelist() == [ZIP_MEMBER]
            assert zf.read(ZIP_MEMBER).decode() == body

    def test_beams_only_model_omits_geometry(self, tmp_path):
        """No plates means no ACIS body — emitting an empty one is not valid."""
        a = ada.Assembly("a") / (ada.Part("p") / ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE200"))
        out = a.to_genie_xml(tmp_path / "beams.xml")
        assert ET.parse(str(out)).getroot().find(".//geometry/sat_embedded_sequence") is None


class TestBeamSatReference:
    """A beam's <wire> must name the SAT edges its axis became.

    Left empty, Genie rebuilds every beam's ACIS wire on import. On a large
    frame that is the single dominant import cost — far more than the plates —
    so an empty reference here is a performance bug, not a cosmetic one.
    """

    @staticmethod
    def _model():
        deck = ada.Plate.from_3d_points("deck", [(0, 0, 0), (3.9, 0, 0), (3.9, 1.3, 0), (0, 1.3, 0)], 0.01)
        # crosses the stiffeners at y=0.65, so each stiffener axis splits in two
        bulk = ada.Plate.from_3d_points("bulk", [(0, 0.65, 0), (3.9, 0.65, 0), (3.9, 0.65, 1.0), (0, 0.65, 1.0)], 0.01)
        stiff = [ada.Beam(f"st{i}", (x, 0, 0), (x, 1.3, 0), "HP180x8") for i, x in enumerate([1.3, 2.6])]
        return ada.Assembly("a") / (ada.Part("p") / ([deck, bulk] + stiff))

    def _refs(self, out):
        return {
            sb.get("name"): [e.get("edge_ref") for e in sb.iterfind(".//wire/sat_reference/edge")]
            for sb in ET.parse(str(out)).getroot().iterfind(".//structure/straight_beam")
        }

    def test_beam_references_every_edge_its_axis_became(self, tmp_path):
        refs = self._refs(self._model().to_genie_xml(tmp_path / "b.xml"))
        assert len(refs["st0"]) == 2  # split by the bulkhead
        assert len(refs["st1"]) == 2
        assert not set(refs["st0"]) & set(refs["st1"])  # no edge claimed twice

    def test_every_edge_ref_resolves_to_a_named_sat_edge(self, tmp_path):
        import re

        from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml

        out = self._model().to_genie_xml(tmp_path / "b.xml")
        named = set(re.findall(r"@12 (EDGE\d{8}) #", get_sat_text_from_xml(out)))
        referenced = {r for v in self._refs(out).values() for r in v}
        assert referenced and referenced <= named
        assert named == referenced  # and no EDGE is named without being referenced

    def test_beam_with_no_plate_under_it_gets_a_wire_body(self, tmp_path):
        """Its axis bounds no face, so it cannot hang off a loop — ACIS carries
        it as a wire body off the shell instead. Without one the beam has no
        geometry to reference and Genie rebuilds it."""
        from tests.core.cadit.sat.sat_topology import parse, ref_errors

        from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml

        a = self._model()
        a.parts["p"].add_beam(ada.Beam("floating", (0, 0, 9), (1, 0, 9), "IPE200"))
        out = a.to_genie_xml(tmp_path / "b.xml")

        refs = self._refs(out)
        assert len(refs["floating"]) == 1
        assert len(refs["st0"]) == 2  # the plate-bound beams are unaffected

        sat = get_sat_text_from_xml(out)
        assert ref_errors(sat) == []
        ents = parse(sat)
        wires = [i for i, (t, _) in ents.items() if t == "wire"]
        assert len(wires) == 1
        # the shell must actually point at it (field[7]), or it is unreachable
        shell = next(f for t, f in ents.values() if t == "shell")
        assert shell[7] == f"${wires[0]}"

    def test_touching_free_beams_share_one_wire(self, tmp_path):
        """Edges meeting at a vertex must all sit in the SAME wire.

        One wire per edge puts that vertex in as many groups as there are edges
        on it, and the model fails ACIS verification with "vertex has edge in
        multiple groups". Genie groups them per connected run; so do we.
        """
        from tests.core.cadit.sat.sat_topology import parse, ref_errors, wire_groups

        from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml

        a = self._model()
        # a closed frame away from any plate, plus a spur off one corner: two
        # runs that never touch each other -> two wires, whatever the edge count
        corners = [(0, 0, 9), (4, 0, 9), (4, 4, 9), (0, 4, 9)]
        for i in range(4):
            a.parts["p"].add_beam(ada.Beam(f"fr{i}", corners[i], corners[(i + 1) % 4], "IPE200"))
        a.parts["p"].add_beam(ada.Beam("spur", (4, 4, 9), (6, 6, 9), "IPE200"))
        a.parts["p"].add_beam(ada.Beam("lonely", (0, 0, 20), (1, 0, 20), "IPE200"))

        sat = get_sat_text_from_xml(a.to_genie_xml(tmp_path / "b.xml"))
        assert ref_errors(sat) == []

        groups = wire_groups(sat)
        assert groups["wires"] == 2  # the 5-beam run, and the lonely one
        assert groups["wires_per_component"] == {1: 2}
        assert groups["vertices_in_multiple_wires"] == 0
        # every vertex's coedges form one closed ring, including the loose ends
        assert groups["fans_that_are_closed_rings"] == groups["fans"]

        ents = parse(sat)
        shell = next(f for t, f in ents.values() if t == "shell")
        wires = [i for i, (t, _) in ents.items() if t == "wire"]
        assert shell[7] == f"${wires[0]}"  # the chain still hangs off the shell

    def test_no_sat_means_no_edge_refs(self, tmp_path):
        out = self._model().to_genie_xml(tmp_path / "b.xml", embed_sat=False)
        assert all(v == [] for v in self._refs(out).values())


class TestCurveOffset:
    """Every <curve_offset> needs a child.

    Genie rejects a bare <curve_offset/> outright ("Unable to build model from
    element"), losing the beam. The default Justification.NA with no
    eccentricity is the overwhelmingly common case, so an empty element there
    takes out most of a model's beams at once.
    """

    @staticmethod
    def _offsets(out):
        return {
            sb.get("name"): [c.tag for c in sb.find("curve_offset")]
            for sb in ET.parse(str(out)).getroot().iterfind(".//structure/straight_beam")
        }

    def _write(self, tmp_path, beam):
        a = ada.Assembly("a") / (ada.Part("p") / beam)
        return self._offsets(a.to_genie_xml(tmp_path / "b.xml"))

    def test_default_beam_states_the_default_rule(self, tmp_path):
        """No offset and no flush intent still has to name a rule."""
        offsets = self._write(tmp_path, ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300"))
        assert offsets["bm"] == ["reparameterized_beam_curve_offset"]

    @pytest.mark.parametrize(
        "justification, expected",
        [
            (Justification.NA, "reparameterized_beam_curve_offset"),
            (Justification.UNSET, "reparameterized_beam_curve_offset"),
            (Justification.TOS, "constant_curve_offset"),
            (Justification.FLUSH_TOP, "aligned_curve_offset"),
            (Justification.FLUSH_BOTTOM, "aligned_curve_offset"),
        ],
    )
    def test_every_justification_writes_exactly_one_child(self, tmp_path, justification, expected):
        beam = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300", justification=justification)
        assert self._write(tmp_path, beam)["bm"] == [expected]

    def test_varying_eccentricity_is_written_numerically(self, tmp_path):
        beam = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300", e1=(0, 0, 0.1))
        assert self._write(tmp_path, beam)["bm"] == ["linear_varying_curve_offset"]

    def test_flush_alignment_matches_the_justification(self, tmp_path):
        for just, alignment in ((Justification.FLUSH_TOP, "flush_top"), (Justification.FLUSH_BOTTOM, "flush_bottom")):
            beam = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300", justification=just)
            a = ada.Assembly("a") / (ada.Part("p") / beam)
            out = a.to_genie_xml(tmp_path / f"{alignment}.xml")
            el = ET.parse(str(out)).getroot().find(".//straight_beam/curve_offset/aligned_curve_offset")
            assert el.get("alignment") == alignment

    def test_no_beam_is_written_with_an_empty_curve_offset(self, tmp_path):
        """The whole point, over a mixed model."""
        beams = [
            ada.Beam("default", (0, 0, 0), (1, 0, 0), "IPE300"),
            ada.Beam("flush", (0, 1, 0), (1, 1, 0), "IPE300", justification=Justification.FLUSH_TOP),
            ada.Beam("ecc", (0, 2, 0), (1, 2, 0), "IPE300", e1=(0, 0, 0.1)),
            ada.Beam("tos", (0, 3, 0), (1, 3, 0), "IPE300", justification=Justification.TOS),
        ]
        a = ada.Assembly("a") / (ada.Part("p") / beams)
        out = a.to_genie_xml(tmp_path / "b.xml")
        offsets = self._offsets(out)
        assert len(offsets) == 4
        assert all(len(v) == 1 for v in offsets.values()), offsets
        assert "<curve_offset/>" not in out.read_text()
        assert "<curve_offset />" not in out.read_text()


class TestGenieXmlFlags:
    @staticmethod
    def _model():
        pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)
        return ada.Assembly("a") / (ada.Part("p") / pl)

    def test_streaming_matches_dom_with_sat(self, tmp_path):
        dom, stream = tmp_path / "dom.xml", tmp_path / "stream.xml"
        self._model().to_genie_xml(dom, embed_sat=True)
        self._model().to_genie_xml(stream, embed_sat=True, streaming=True)
        assert dom.read_bytes() == stream.read_bytes()

    def test_embed_sat_with_merge_strategy_raises(self, tmp_path):
        """Contradictory: the SAT body is built from Plate objects, which the
        merge_strategy face source never materialises. It used to silently drop
        merge_strategy (DOM path) or embed_sat (streaming path)."""
        with pytest.raises(ValueError, match="incompatible with merge_strategy"):
            self._model().to_genie_xml(tmp_path / "x.xml", embed_sat=True, streaming=True, merge_strategy="coplanar")

    def test_merge_strategy_without_streaming_raises(self, tmp_path):
        """merge_strategy is only honoured on the streaming path; it used to be
        accepted and ignored."""
        with pytest.raises(ValueError, match="only honoured on the streaming path"):
            self._model().to_genie_xml(tmp_path / "x.xml", merge_strategy="coplanar")

    def test_merge_strategy_alone_falls_back_to_polygons(self, tmp_path):
        out = self._model().to_genie_xml(tmp_path / "x.xml", streaming=True, merge_strategy="coplanar")
        assert "<sat_embedded_sequence" not in out.read_text()

    def test_embed_sat_false_writes_polygons(self, tmp_path):
        out = self._model().to_genie_xml(tmp_path / "x.xml", embed_sat=False)
        raw = out.read_text()
        assert "<polygons>" in raw
        assert "<sat_embedded_sequence" not in raw


def test_create_groups_split_across_parts(tmp_path):
    p1 = ada.Part("P1") / (ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE200"))
    p2 = ada.Part("P2") / (ada.Beam("bm2", (0, 0, 1), (1, 0, 1), "IPE200"))
    p1.add_group("group1", [p1.beams[0]])
    p2.add_group("group1", [p2.beams[0]])

    a = ada.Assembly("a") / (p1, p2)

    dest = tmp_path / "groups_split_across_parts.xml"

    a.to_genie_xml(dest, embed_sat=False)

    tree = ET.parse(dest)
    root = tree.getroot()
    sets = root.find("./model/structure_domain/sets")

    assert sets is not None

    assert len(sets.findall("./set")) == 1

    assert len(sets.findall("./set/concepts/concept")) == 2
    assert sets.find("./set/concepts/concept[@concept_ref='bm1']") is not None
    assert sets.find("./set/concepts/concept[@concept_ref='bm2']") is not None


def test_check_placement_of_parts(tmp_path):
    start = (0, 0, 0)
    end = (5, 0, 0)
    end2 = (10, 0, 0)
    end3 = (15, 0, 0)

    bm_plates_1 = ada.Beam("bm3", end2, end3, "BG200x200x8x8").to_plates()

    # Part 1
    bm1 = ada.Beam("bm1", start, end, "IPE200")
    bm2 = ada.Beam("bm2", end, end2, "IPE200")
    p1 = ada.Part("P1") / (bm1, bm2, *bm_plates_1)
    p1.add_mass(ada.MassPoint("m1", end, 100))
    p1.concept_fem.constraints.add_point_constraint(
        ada.ConstraintConceptPoint("bc1", start, ada.ConstraintConceptDofType.encastre())
    )
    lc1 = p1.concept_fem.loads.add_load_case(
        ada.LoadConceptCase(
            "LC1",
            fem_loadcase_number=32,
            mesh_loads_as_mass=True,
            loads=[
                ada.LoadConceptPoint(name="PointLoad1", position=end, force=(0, 0, -50), moment=(0, 0, 0)),
                ada.LoadConceptLine(
                    name="LineLoad1",
                    start_point=start,
                    end_point=end,
                    intensity_start=(0, 0, -50),
                    intensity_end=(0, 0, -50),
                ),
                ada.LoadConceptSurface(
                    name="SurfaceLoad1",
                    points=[(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)],
                    pressure=2000,
                    side="front",
                ),
                ada.LoadConceptAccelerationField(
                    name="AccelFieldLoad1",
                    acceleration=(0, 0, -9.81),
                    include_self_weight=True,
                    rotational_field=ada.RotationalAccelerationField(start, bm1.xvec, 0.01, 0.04),
                ),
            ],
        )
    )
    p1.concept_fem.loads.add_load_case_combination(
        ada.LoadConceptCaseCombination(
            "LCC1",
            load_cases=[ada.LoadConceptCaseFactored(lc1, factor=1.5, phase=0)],
            design_condition="operating",
            complex_type="static",
            convert_load_to_mass=False,
            global_scale_factor=1.0,
            equipments_type="line_load",
        )
    )
    p1.concept_fem.constraints.add_rigid_link(
        ada.ConstraintConceptRigidLink(
            "rigid_link1",
            end2,
            ada.RigidLinkRegion.from_center_and_offset(end2, (1, 1, 1)),
            ada.ConstraintConceptDofType.encastre("dependent"),
        )
    )
    p1.add_part(ada.Equipment("eq1", 50, (0, 0, 1), end3, 1, 1, 1, EquipRepr.LINE_LOAD, lc1))

    # Part 2
    bm3 = ada.Beam("bm3", start, end, "IPE200")
    bm4 = ada.Beam("bm4", end, end2, "IPE200")
    bm_plates_2 = ada.Beam("bm4", end2, end3, "BG200x200x8x8").to_plates()
    p2 = ada.Part("P2") / (bm3, bm4, *bm_plates_2)
    p2.placement = ada.Placement(origin=(0, 0, 10))
    p2.add_mass(ada.MassPoint("m2", end, 100))
    p2.concept_fem.constraints.add_point_constraint(
        ada.ConstraintConceptPoint("bc2", start, ada.ConstraintConceptDofType.encastre())
    )
    lc2 = p2.concept_fem.loads.add_load_case(
        ada.LoadConceptCase(
            "LC2",
            fem_loadcase_number=33,
            loads=[
                ada.LoadConceptPoint(name="PointLoad2", position=end, force=(0, 0, -50), moment=(0, 0, 0)),
                ada.LoadConceptLine(
                    name="LineLoad2",
                    start_point=start,
                    end_point=end,
                    intensity_start=(0, 0, -50),
                    intensity_end=(0, 0, -50),
                ),
                ada.LoadConceptSurface(
                    name="SurfaceLoad2",
                    points=[(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)],
                    pressure=2000,
                    side="front",
                ),
                ada.LoadConceptAccelerationField(
                    name="AccelFieldLoad2",
                    acceleration=(0, 0, -9.81),
                    include_self_weight=True,
                    rotational_field=ada.RotationalAccelerationField(start, bm1.xvec, 0.01, 0.04),
                ),
            ],
        )
    )
    p2.concept_fem.loads.add_load_case_combination(
        ada.LoadConceptCaseCombination(
            "LCC2",
            load_cases=[ada.LoadConceptCaseFactored(lc2, factor=1.5, phase=0)],
            design_condition="operating",
            complex_type="static",
            convert_load_to_mass=False,
            global_scale_factor=1.0,
            equipments_type="line_load",
        )
    )
    p2.concept_fem.constraints.add_rigid_link(
        ada.ConstraintConceptRigidLink(
            "rigid_link2",
            end2,
            ada.RigidLinkRegion.from_center_and_offset(end2, (1, 1, 1)),
            ada.ConstraintConceptDofType.encastre("dependent"),
        )
    )
    p2.add_part(ada.Equipment("eq2", 50, (0, 0, 1), end3, 1, 1, 1, EquipRepr.LINE_LOAD, lc2))

    a = ada.Assembly("a") / (p1, p2)
    dest = a.to_genie_xml(tmp_path / "offset_parts.xml", embed_sat=False)

    with open(dest, "r") as f:
        xml_str = f.read()

    xml_root = ET.fromstring(xml_str)

    # Test 1: Validate support points exist and have correct positions
    support_points = xml_root.findall(".//support_point")
    assert len(support_points) == 2, "Should have 2 support points (bc1 and bc2)"

    # Check bc1 (Part 1) - should be at origin (0,0,0)
    bc1_point = next(sp for sp in support_points if sp.get("name") == "bc1")
    bc1_position = bc1_point.find(".//position")
    assert bc1_position.get("x") == "0.0"
    assert bc1_position.get("y") == "0.0"
    assert bc1_position.get("z") == "0.0"

    # Check bc2 (Part 2) - should be at (0,0,10) due to placement offset
    bc2_point = next(sp for sp in support_points if sp.get("name") == "bc2")
    bc2_position = bc2_point.find(".//position")
    assert bc2_position.get("x") == "0.0"
    assert bc2_position.get("y") == "0.0"
    assert bc2_position.get("z") == "10.0"

    # Test 2: Validate boundary conditions for support points
    for sp in support_points:
        boundary_conditions = sp.findall(".//boundary_condition")
        assert len(boundary_conditions) == 6, "Should have 6 DOF constraints (encastre)"

        # Check all DOFs are fixed for encastre condition
        dofs = {bc.get("dof"): bc.get("constraint") for bc in boundary_conditions}
        expected_dofs = {"dx": "fixed", "dy": "fixed", "dz": "fixed", "rx": "fixed", "ry": "fixed", "rz": "fixed"}
        assert dofs == expected_dofs, "DOF constraints should match encastre condition"

    # Test 3: Validate rigid links exist and have correct positions
    rigid_links = xml_root.findall(".//support_rigid_link")
    assert len(rigid_links) == 2, "Should have 2 rigid links"

    # Check rigid_link1 (Part 1) - should be at end2 (10,0,0)
    rl1 = next(rl for rl in rigid_links if rl.get("name") == "rigid_link1")
    rl1_position = rl1.find(".//position")
    assert rl1_position.get("x") == "10.0"
    assert rl1_position.get("y") == "0.0"
    assert rl1_position.get("z") == "0.0"

    # Check rigid_link2 (Part 2) - should be at end2 + offset (10,0,10)
    rl2 = next(rl for rl in rigid_links if rl.get("name") == "rigid_link2")
    rl2_position = rl2.find(".//position")
    assert rl2_position.get("x") == "10.0"
    assert rl2_position.get("y") == "0.0"
    assert rl2_position.get("z") == "10.0"

    # Test 4: Validate rigid link attributes
    for rl in rigid_links:
        assert rl.get("include_all_edges") == "true"
        assert rl.get("rotation_dependent") == "true"

        # Check footprint box region exists
        footprint_box = rl.find(".//footprint_box")
        assert footprint_box is not None, "Rigid link should have footprint_box"

        lower_corner = footprint_box.find("lower_corner")
        upper_corner = footprint_box.find("upper_corner")
        assert lower_corner is not None and upper_corner is not None

    # Test 5: Validate rigid link boundary conditions
    for rl in rigid_links:
        boundary_conditions = rl.findall(".//boundary_condition")
        assert len(boundary_conditions) == 6, "Rigid link should have 6 DOF constraints"

        # Check all DOFs are dependent for rigid link
        dofs = {bc.get("dof"): bc.get("constraint") for bc in boundary_conditions}
        expected_dofs = {
            "dx": "dependent",
            "dy": "dependent",
            "dz": "dependent",
            "rx": "dependent",
            "ry": "dependent",
            "rz": "dependent",
        }
        assert dofs == expected_dofs, "Rigid link DOFs should be dependent"

    # Test 6: Validate mass points exist (if they create XML elements)
    point_masses = xml_root.findall(".//point_mass")
    assert len(point_masses) == 2, "Should have 2 point masses"

    mass_m1 = [m for m in point_masses if m.get("name") == "m1"][0]
    # Assert that the position of m1 is at end
    mass_m1_position = mass_m1.find(".//position")
    assert mass_m1_position.get("x") == "5.0"
    assert mass_m1_position.get("y") == "0.0"
    assert mass_m1_position.get("z") == "0.0"

    mass_m2 = [m for m in point_masses if m.get("name") == "m2"][0]
    # Assert that the position of m1 is at end
    mass_m2_position = mass_m2.find(".//position")
    assert mass_m2_position.get("x") == "5.0"
    assert mass_m2_position.get("y") == "0.0"
    assert mass_m2_position.get("z") == "10.0"

    # Test 7: Validate load cases exist
    load_cases = xml_root.findall(".//loadcase_basic")
    assert len(load_cases) == 2, "Should have at least 2 load cases (LC1 and LC2)"

    # Test 8: Validate load case combinations
    load_combinations = xml_root.findall(".//loadcase_combination")
    assert len(load_combinations) == 2, "Should have at least 2 load combinations"

    # Test 9: Validate beams exist with correct placement
    beams = xml_root.findall(".//straight_beam")
    assert len(beams) == 4, "Should have 4 beams"
    # Should have beams from both parts, check if positions are correctly offset

    # Test 10: Validate local coordinate systems exist
    local_systems = xml_root.findall(".//local_system")
    assert len(local_systems) > 0, "Should have local coordinate systems"

    # Test 11: Validate structure domain contains proper elements
    structure_domain = xml_root.find(".//structure_domain")
    assert structure_domain is not None, "Should have structure_domain"

    structures = structure_domain.find("structures")
    assert structures is not None, "Should have structures element"

    # Test 12: Validate materials and sections are present
    properties = structure_domain.find("properties")
    assert properties is not None, "Should have properties section"

    materials = properties.findall(".//material")
    sections = properties.findall(".//section")
    assert len(materials) > 0, "Should have materials defined"
    assert len(sections) > 0, "Should have sections defined"

    dummy_meshes = xml_root.findall(".//dummy_mesh_loads_as_mass")
    assert len(dummy_meshes) == 2, "Should have 2 dummy mesh"

    assert dummy_meshes[0].get("mesh_loads_as_mass") == "true", "Dummy mesh should be enabled"

    # Do not add these in prod
    # a.show()
    # from ada.cadit.gxml.utils import start_genie
    #
    # start_genie(dest)


def test_basic_hinges(tmp_path):
    fixed = ada.BeamHinge.encastre("fixed")
    pinned = ada.BeamHinge.pinned("pinned")

    bm1 = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300", hi1=fixed, hi2=pinned)
    a = ada.Assembly("myassembly") / bm1
    dest = a.to_genie_xml(tmp_path / "basic_hinges.xml", embed_sat=False)

    with open(dest, "r") as f:
        xml_str = f.read()

    xml_root = ET.fromstring(xml_str)

    flexible_hinges = xml_root.findall(".//flexible_hinge")
    assert len(flexible_hinges) == 2, "Should have 2 flexible hinges"

    # Do not add these in prod
    # os.startfile(dest)
    # a.show()
    # from ada.cadit.gxml.utils import start_genie
    #
    # start_genie(dest, run_externally=True)


def test_multi_node_bc_writes_per_node_support_points(tmp_path):
    # A FEM Bc applied over a multi-node nset must write one support_point per node
    # (mechanically exact, no rigid coupling). Single-node BCs keep the bare name;
    # multi-node BCs get a 1-based suffix. Regression for the previously-raised
    # NotImplementedError in add_fem_boundary_conditions.
    from ada import Node
    from ada.fem import Bc, FemSet

    p = ada.Part("MyPart")
    fem = p.fem
    nodes = [Node([float(i), 0.0, 0.0], i + 1) for i in range(3)]
    for n in nodes:
        fem.nodes.add(n)

    fs = fem.add_set(FemSet("BottomNodes", nodes, FemSet.TYPES.NSET))
    fem.add_bc(Bc("Fix", fs, [1, 2, 3]))  # dx, dy, dz fixed; rotations free

    a = ada.Assembly("a") / p
    dest = a.to_genie_xml(tmp_path / "multi_node_bc.xml", embed_sat=False)

    xml_root = ET.fromstring(dest.read_text())
    support_points = xml_root.findall(".//support_point")
    assert len(support_points) == 3, "One support_point per node in the BC's nset"

    names = sorted(sp.get("name") for sp in support_points)
    assert names == ["Fix_1", "Fix_2", "Fix_3"]

    # Positions follow the node coordinates
    positions = sorted(float(sp.find(".//position").get("x")) for sp in support_points)
    assert positions == [0.0, 1.0, 2.0]

    # DOFs match the BC ([1,2,3] fixed -> translations fixed, rotations free) on every point
    for sp in support_points:
        dofs = {bc.get("dof"): bc.get("constraint") for bc in sp.findall(".//boundary_condition")}
        assert dofs == {
            "dx": "fixed",
            "dy": "fixed",
            "dz": "fixed",
            "rx": "free",
            "ry": "free",
            "rz": "free",
        }

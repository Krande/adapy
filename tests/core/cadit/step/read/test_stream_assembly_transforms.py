"""STEP assembly-instance transforms in the kernel-free streaming reader.

A solid authored in a sub-assembly's *local* frame is placed in the world by an
ABSR (geom rep) <- SHAPE_REPRESENTATION_RELATIONSHIP -> placement rep, where the
placement rep is rep_1 of a CONTEXT_DEPENDENT_SHAPE_REPRESENTATION carrying an
ITEM_DEFINED_TRANSFORMATION. The reader resolves that chain to a world 4x4 matrix
(validated vs OpenCascade to 1e-9) and yields one placed Geometry per instance.

The fixtures are authored inline (written to tmp_path) so the test never depends on
files under /tmp.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.cadit.step.read.stream_reader import stream_read_step

# A unit cube authored at the origin in its own local frame, as a complete analytic
# B-rep (MANIFOLD_SOLID_BREP #159) that is the item of an ADVANCED_BREP_SHAPE_REPRESENTATION
# (#172). This is exactly the structure adapy's own AP242 stream emitter produces for a
# PrimBox; the assembly tail (#178..#191) wires the geom rep to a placement SHAPE_REPRESENTATION
# (#179) via the CDSR/rep_rel/IDT chain. The IDT placements ({CHILD}/{PARENT}) are templated
# so a test can inject a known non-identity transform.
_CUBE_TEMPLATE = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('assembly transform fixture'),'2;1');
FILE_NAME('a','1970-01-01T00:00:00',(''),(''),'fixture','adapy','');
FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }'));
ENDSEC;
DATA;
#1=APPLICATION_CONTEXT('managed model based 3d engineering');
#2=APPLICATION_PROTOCOL_DEFINITION('international standard','ap242_managed_model_based_3d_engineering_mim_lf',2014,#1);
#3=PRODUCT_CONTEXT('',#1,'mechanical');
#4=PRODUCT_DEFINITION_CONTEXT('part definition',#1,'design');
#5=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.));
#6=(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.));
#7=(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT());
#8=UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-06),#5,'distance_accuracy_value','edge curve and vertex accuracy');
#9=(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#8))GLOBAL_UNIT_ASSIGNED_CONTEXT((#5,#6,#7))REPRESENTATION_CONTEXT('Context','3D'));
#10=CARTESIAN_POINT('',(0.,0.,0.));
#11=CARTESIAN_POINT('',(1.,0.,0.));
#12=CARTESIAN_POINT('',(1.,1.,0.));
#13=CARTESIAN_POINT('',(0.,1.,0.));
#14=CARTESIAN_POINT('',(0.,0.,1.));
#15=CARTESIAN_POINT('',(1.,0.,1.));
#16=CARTESIAN_POINT('',(1.,1.,1.));
#17=CARTESIAN_POINT('',(0.,1.,1.));
#18=VERTEX_POINT('',#10);
#19=VERTEX_POINT('',#11);
#20=VERTEX_POINT('',#12);
#21=VERTEX_POINT('',#13);
#22=VERTEX_POINT('',#14);
#23=VERTEX_POINT('',#15);
#24=VERTEX_POINT('',#16);
#25=VERTEX_POINT('',#17);
#26=DIRECTION('',(0.,0.,1.));
#27=VECTOR('',#26,1.);
#28=CARTESIAN_POINT('',(0.,0.,0.));
#29=LINE('',#28,#27);
#30=EDGE_CURVE('',#18,#22,#29,.T.);
#31=DIRECTION('',(0.,0.,1.));
#32=VECTOR('',#31,1.);
#33=CARTESIAN_POINT('',(1.,0.,0.));
#34=LINE('',#33,#32);
#35=EDGE_CURVE('',#19,#23,#34,.T.);
#36=DIRECTION('',(0.,0.,1.));
#37=VECTOR('',#36,1.);
#38=CARTESIAN_POINT('',(1.,1.,0.));
#39=LINE('',#38,#37);
#40=EDGE_CURVE('',#20,#24,#39,.T.);
#41=DIRECTION('',(0.,0.,1.));
#42=VECTOR('',#41,1.);
#43=CARTESIAN_POINT('',(0.,1.,0.));
#44=LINE('',#43,#42);
#45=EDGE_CURVE('',#21,#25,#44,.T.);
#46=DIRECTION('',(1.,0.,0.));
#47=VECTOR('',#46,1.);
#48=CARTESIAN_POINT('',(0.,0.,0.));
#49=LINE('',#48,#47);
#50=EDGE_CURVE('',#18,#19,#49,.T.);
#51=DIRECTION('',(1.,0.,0.));
#52=VECTOR('',#51,1.);
#53=CARTESIAN_POINT('',(0.,0.,1.));
#54=LINE('',#53,#52);
#55=EDGE_CURVE('',#22,#23,#54,.T.);
#56=CARTESIAN_POINT('',(0.,0.,0.));
#57=DIRECTION('',(0.,-1.,0.));
#58=DIRECTION('',(1.,0.,0.));
#59=AXIS2_PLACEMENT_3D('',#56,#57,#58);
#60=PLANE('',#59);
#61=ORIENTED_EDGE('',*,*,#50,.T.);
#62=ORIENTED_EDGE('',*,*,#35,.T.);
#63=ORIENTED_EDGE('',*,*,#55,.F.);
#64=ORIENTED_EDGE('',*,*,#30,.F.);
#65=EDGE_LOOP('',(#61,#62,#63,#64));
#66=FACE_OUTER_BOUND('',#65,.T.);
#67=ADVANCED_FACE('',(#66),#60,.T.);
#68=DIRECTION('',(0.,1.,0.));
#69=VECTOR('',#68,1.);
#70=CARTESIAN_POINT('',(1.,0.,0.));
#71=LINE('',#70,#69);
#72=EDGE_CURVE('',#19,#20,#71,.T.);
#73=DIRECTION('',(0.,1.,0.));
#74=VECTOR('',#73,1.);
#75=CARTESIAN_POINT('',(1.,0.,1.));
#76=LINE('',#75,#74);
#77=EDGE_CURVE('',#23,#24,#76,.T.);
#78=CARTESIAN_POINT('',(1.,0.,0.));
#79=DIRECTION('',(1.,0.,0.));
#80=DIRECTION('',(0.,1.,0.));
#81=AXIS2_PLACEMENT_3D('',#78,#79,#80);
#82=PLANE('',#81);
#83=ORIENTED_EDGE('',*,*,#72,.T.);
#84=ORIENTED_EDGE('',*,*,#40,.T.);
#85=ORIENTED_EDGE('',*,*,#77,.F.);
#86=ORIENTED_EDGE('',*,*,#35,.F.);
#87=EDGE_LOOP('',(#83,#84,#85,#86));
#88=FACE_OUTER_BOUND('',#87,.T.);
#89=ADVANCED_FACE('',(#88),#82,.T.);
#90=DIRECTION('',(-1.,0.,0.));
#91=VECTOR('',#90,1.);
#92=CARTESIAN_POINT('',(1.,1.,0.));
#93=LINE('',#92,#91);
#94=EDGE_CURVE('',#20,#21,#93,.T.);
#95=DIRECTION('',(-1.,0.,0.));
#96=VECTOR('',#95,1.);
#97=CARTESIAN_POINT('',(1.,1.,1.));
#98=LINE('',#97,#96);
#99=EDGE_CURVE('',#24,#25,#98,.T.);
#100=CARTESIAN_POINT('',(1.,1.,0.));
#101=DIRECTION('',(0.,1.,-0.));
#102=DIRECTION('',(-1.,0.,0.));
#103=AXIS2_PLACEMENT_3D('',#100,#101,#102);
#104=PLANE('',#103);
#105=ORIENTED_EDGE('',*,*,#94,.T.);
#106=ORIENTED_EDGE('',*,*,#45,.T.);
#107=ORIENTED_EDGE('',*,*,#99,.F.);
#108=ORIENTED_EDGE('',*,*,#40,.F.);
#109=EDGE_LOOP('',(#105,#106,#107,#108));
#110=FACE_OUTER_BOUND('',#109,.T.);
#111=ADVANCED_FACE('',(#110),#104,.T.);
#112=DIRECTION('',(0.,-1.,0.));
#113=VECTOR('',#112,1.);
#114=CARTESIAN_POINT('',(0.,1.,0.));
#115=LINE('',#114,#113);
#116=EDGE_CURVE('',#21,#18,#115,.T.);
#117=DIRECTION('',(0.,-1.,0.));
#118=VECTOR('',#117,1.);
#119=CARTESIAN_POINT('',(0.,1.,1.));
#120=LINE('',#119,#118);
#121=EDGE_CURVE('',#25,#22,#120,.T.);
#122=CARTESIAN_POINT('',(0.,1.,0.));
#123=DIRECTION('',(-1.,0.,0.));
#124=DIRECTION('',(0.,-1.,0.));
#125=AXIS2_PLACEMENT_3D('',#122,#123,#124);
#126=PLANE('',#125);
#127=ORIENTED_EDGE('',*,*,#116,.T.);
#128=ORIENTED_EDGE('',*,*,#30,.T.);
#129=ORIENTED_EDGE('',*,*,#121,.F.);
#130=ORIENTED_EDGE('',*,*,#45,.F.);
#131=EDGE_LOOP('',(#127,#128,#129,#130));
#132=FACE_OUTER_BOUND('',#131,.T.);
#133=ADVANCED_FACE('',(#132),#126,.T.);
#134=CARTESIAN_POINT('',(0.,0.,1.));
#135=DIRECTION('',(0.,0.,1.));
#136=DIRECTION('',(1.,0.,0.));
#137=AXIS2_PLACEMENT_3D('',#134,#135,#136);
#138=PLANE('',#137);
#139=ORIENTED_EDGE('',*,*,#55,.T.);
#140=ORIENTED_EDGE('',*,*,#77,.T.);
#141=ORIENTED_EDGE('',*,*,#99,.T.);
#142=ORIENTED_EDGE('',*,*,#121,.T.);
#143=EDGE_LOOP('',(#139,#140,#141,#142));
#144=FACE_OUTER_BOUND('',#143,.T.);
#145=ADVANCED_FACE('',(#144),#138,.T.);
#146=CARTESIAN_POINT('',(0.,0.,0.));
#147=DIRECTION('',(-0.,-0.,-1.));
#148=DIRECTION('',(1.,0.,0.));
#149=AXIS2_PLACEMENT_3D('',#146,#147,#148);
#150=PLANE('',#149);
#151=ORIENTED_EDGE('',*,*,#116,.F.);
#152=ORIENTED_EDGE('',*,*,#94,.F.);
#153=ORIENTED_EDGE('',*,*,#72,.F.);
#154=ORIENTED_EDGE('',*,*,#50,.F.);
#155=EDGE_LOOP('',(#151,#152,#153,#154));
#156=FACE_OUTER_BOUND('',#155,.T.);
#157=ADVANCED_FACE('',(#156),#150,.T.);
#158=CLOSED_SHELL('',(#67,#89,#111,#133,#145,#157));
#159=MANIFOLD_SOLID_BREP('box',#158);
#168=CARTESIAN_POINT('',(0.,0.,0.));
#169=DIRECTION('',(0.,0.,1.));
#170=DIRECTION('',(1.,0.,0.));
#171=AXIS2_PLACEMENT_3D('',#168,#169,#170);
#172=ADVANCED_BREP_SHAPE_REPRESENTATION('box',(#171,#159),#9);
#173=PRODUCT('box','box','',(#3));
#174=PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#173));
#175=PRODUCT_DEFINITION_FORMATION('','',#173);
#176=PRODUCT_DEFINITION('design','',#175,#4);
#177=PRODUCT_DEFINITION_SHAPE('','',#176);
#178=SHAPE_DEFINITION_REPRESENTATION(#177,#172);
#179=SHAPE_REPRESENTATION('a',(#171),#9);
#180=PRODUCT('a','a','',(#3));
#181=PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#180));
#182=PRODUCT_DEFINITION_FORMATION('','',#180);
#183=PRODUCT_DEFINITION('design','',#182,#4);
#184=PRODUCT_DEFINITION_SHAPE('','',#183);
#185=SHAPE_DEFINITION_REPRESENTATION(#184,#179);
#186=NEXT_ASSEMBLY_USAGE_OCCURRENCE('1','box','',#183,#176,$);
#187=PRODUCT_DEFINITION_SHAPE('','',#186);
@@IDT_BLOCK@@
ENDSEC;
END-ISO-10303-21;
"""


def _render(idt_block: str) -> str:
    # The FILE_SCHEMA header contains literal '{ }', so substitute by marker rather than
    # str.format (which would treat those braces as fields).
    return _CUBE_TEMPLATE.replace("@@IDT_BLOCK@@", idt_block)


def _axis2_lines(base: int, loc, axis, ref) -> str:
    """Emit an AXIS2_PLACEMENT_3D #base + its 3 sub-entities (#base+1..+3)."""

    def _d(v):
        return "(" + ",".join(f"{x:.10g}." if float(x) == int(x) else f"{x:.10g}" for x in v) + ")"

    return (
        f"#{base}=AXIS2_PLACEMENT_3D('',#{base + 1},#{base + 2},#{base + 3});\n"
        f"#{base + 1}=CARTESIAN_POINT('',{_d(loc)});\n"
        f"#{base + 2}=DIRECTION('',{_d(axis)});\n"
        f"#{base + 3}=DIRECTION('',{_d(ref)});\n"
    )


def _idt_block(child_placement, parent_placement) -> str:
    """Build the IDT/rep_rel/CDSR tail that places geom rep #172 in the world.

    ``child_placement`` is item_1 (lives in rep_1 = the geom rep #172); ``parent_placement``
    is item_2 (lives in rep_2 = the placement rep #179). T_edge = inv(M_child) @ M_parent.
    Each placement is (loc, axis, ref) raw 3-tuples.
    """
    child = _axis2_lines(200, *child_placement)
    parent = _axis2_lines(210, *parent_placement)
    tail = (
        "#188=ITEM_DEFINED_TRANSFORMATION('','',#200,#210);\n"
        "#189=(REPRESENTATION_RELATIONSHIP('','',#172,#179)"
        "REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#188)SHAPE_REPRESENTATION_RELATIONSHIP());\n"
        "#190=CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#189,#187);\n"
    )
    return child + parent + tail


def _axis2_to_matrix(loc, axis, ref) -> np.ndarray:
    z = np.asarray(axis, float)
    z = z / np.linalg.norm(z)
    x = np.asarray(ref, float)
    x = x - np.dot(x, z) * z
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, np.asarray(loc, float)
    return m


def _shell_local_pts(geom) -> np.ndarray:
    pts = []
    for f in geom.geometry.cfs_faces:
        for fb in f.bounds:
            for oe in fb.bound.edge_list:
                for p in (oe.start, oe.end):
                    pts.append([float(p[0]), float(p[1]), float(p[2])])
    return np.asarray(pts, float)


def _apply(pts: np.ndarray, m) -> np.ndarray:
    if m is None:
        return pts
    m = np.asarray(m)
    return pts @ m[:3, :3].T + m[:3, 3]


def _shell_world_pts(geom, k: int = 0) -> np.ndarray:
    """World points of instance ``k`` (geom carries a list of placement matrices)."""
    m = geom.transforms[k] if geom.transforms else None
    return _apply(_shell_local_pts(geom), m)


def test_stream_reader_applies_known_assembly_transform(tmp_path):
    # Place the local-frame unit cube by a known non-trivial transform: parent frame
    # rotated 90deg about Z and translated, child frame at identity. The world matrix
    # the reader resolves must equal inv(M_child) @ M_parent, and the cube's world
    # corners must be the local corners pushed through it.
    child = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))  # identity frame
    parent = ((3.0, 5.0, 7.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))  # +90deg about Z, translated
    step = _render(_idt_block(child, parent))
    path = tmp_path / "asm.step"
    path.write_text(step)

    (geom,) = list(stream_read_step(path, local_pool=False))
    assert geom.transforms is not None and len(geom.transforms) == 1  # single instance

    expected = np.linalg.inv(_axis2_to_matrix(*child)) @ _axis2_to_matrix(*parent)
    assert np.allclose(geom.transforms[0], expected, atol=1e-9)

    # The cube's world corners must be exactly the local [0,1]^3 corners pushed through
    # the resolved transform (independent cross-check of the applied placement).
    local_corners = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float)
    exp_world = local_corners @ expected[:3, :3].T + expected[:3, 3]
    pts = _shell_world_pts(geom)
    assert np.allclose(pts.min(0), exp_world.min(0), atol=1e-9)
    assert np.allclose(pts.max(0), exp_world.max(0), atol=1e-9)
    # The placement is genuinely non-trivial: the rotation moves the cube off its local
    # frame (a +90deg-about-Z parent means world x != local x).
    assert not np.allclose(expected, np.eye(4))


def test_stream_world_bbox_matches_occ(tmp_path):
    # The reader's transformed world bbox must match OpenCascade's STEPControl_Reader
    # bbox for the same file (both in metres) to ~1e-6.
    pytest.importorskip("OCC.Core.Bnd")
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib

    child = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    parent = ((3.0, 5.0, 7.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))  # +90 about X
    step = _render(_idt_block(child, parent))
    path = tmp_path / "asm_occ.step"
    path.write_text(step)

    (geom,) = list(stream_read_step(path, local_pool=False))
    stream_pts = _shell_world_pts(geom)
    smin, smax = stream_pts.min(0), stream_pts.max(0)

    a = ada.from_step(path, reader="occ")
    bb = Bnd_Box()
    n = 0
    for o in a.get_all_physical_objects():
        try:
            shp = o.solid_occ()
        except Exception:  # noqa: BLE001
            continue
        brepbndlib.Add(shp, bb)
        n += 1
    assert n >= 1
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    assert np.allclose(smin, [xmin, ymin, zmin], atol=1e-6)
    assert np.allclose(smax, [xmax, ymax, zmax], atol=1e-6)


def test_stream_reader_multi_instance_two_placements(tmp_path):
    # One product placed TWICE -> two CDSR edges on the same placement rep #179 -> the
    # reader yields two geometries with distinct ids and distinct world transforms.
    child = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    parent_a = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))  # identity -> at origin
    parent_b = ((10.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))  # translated +10x
    block = _idt_block(child, parent_a)
    # A second placement of the SAME geom rep #172 via a fresh IDT/rep_rel/CDSR using
    # the same rep_1=#172 / rep_2=#179, so #179 is rep_1 of two edges -> two instances.
    child2 = _axis2_lines(300, *child)
    parent2 = _axis2_lines(310, *parent_b)
    block += (
        child2 + parent2 + "#388=ITEM_DEFINED_TRANSFORMATION('','',#300,#310);\n"
        "#389=(REPRESENTATION_RELATIONSHIP('','',#172,#179)"
        "REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#388)SHAPE_REPRESENTATION_RELATIONSHIP());\n"
        "#390=CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#389,#187);\n"
    )
    step = _render(block)
    path = tmp_path / "multi.step"
    path.write_text(step)

    geos = list(stream_read_step(path, local_pool=False))
    # One unique solid -> one Geometry carrying BOTH placement matrices (meshed once,
    # placed twice). The /2 instance id is materialised downstream in the tessellator.
    assert len(geos) == 1
    (g,) = geos
    assert g.id == "box"
    assert g.transforms is not None and len(g.transforms) == 2

    centroids = [_shell_world_pts(g, k).mean(0) for k in range(2)]
    # The two instances sit 10 units apart in x (one at origin, one at +10x).
    assert abs((centroids[1] - centroids[0])[0]) == pytest.approx(10.0, abs=1e-9)
    # Distinct world positions.
    assert not np.allclose(centroids[0], centroids[1])

    # End-to-end (tessellate-once): the streamed GLB meshes the solid ONCE yet contains
    # BOTH placements — the merged mesh spans the 10-unit gap between the instances.
    import trimesh

    from ada.cadit.step.stream_to_glb import stream_step_to_glb

    glb = tmp_path / "multi.glb"
    stats = stream_step_to_glb(path, glb, tolerant=True)
    assert stats["meshed"] == 1  # ONE unique solid tessellated, despite two placements
    scene = trimesh.load(glb)
    span = scene.bounds[1] - scene.bounds[0]
    assert max(span) >= 10.0  # both instances present (single box is ~1 unit wide)


def test_stream_reader_flat_emitter_no_transform(tmp_path):
    # Regression: an adapy-emitted flat model has identity IDTs (or no assembly tree),
    # so every solid stream-reads with transform=None and its authored world position —
    # no behaviour change for existing flat files.
    box = ada.PrimBox("box", (1.0, 2.0, 3.0), (2.0, 3.0, 4.0))
    a = ada.Assembly("m") / (ada.Part("p") / box)

    for writer in ("stream", "occ"):
        out = tmp_path / f"flat_{writer}.stp"
        a.to_stp(out, writer=writer) if writer == "stream" else a.to_stp(out)
        geos = list(stream_read_step(out, local_pool=False))
        assert len(geos) == 1
        (g,) = geos
        # The whole point: no assembly tree / identity IDTs -> transforms stays None.
        assert g.transforms is None
        # And the authored geometry is unmoved: the unit box's min corner sits at
        # (1,2,3) and max at (2,3,4) in the file's length unit (adapy writes metres, so
        # the emitter scales mm->m; assert the shape is intact regardless of that scale).
        pts = _shell_world_pts(g)
        lo, hi = pts.min(0), pts.max(0)
        scale = hi[0] - lo[0]  # the unit box's x-extent in the file's length unit
        assert scale > 0
        assert np.allclose(lo / scale, [1.0, 2.0, 3.0], atol=1e-6)
        assert np.allclose(hi / scale, [2.0, 3.0, 4.0], atol=1e-6)


def test_stream_glb_groups_instances_under_assembly_tree(tmp_path):
    """The GLB's id_hierarchy must mirror the STEP product tree — group nodes named
    from the PRODUCTs along each instance's placement chain ('a' -> 'box' here), with
    the mesh instances parented under the deepest group — so a viewer can fold whole
    sub-assemblies instead of scrolling a flat list."""
    import json
    import struct

    from ada.cadit.step.stream_to_glb import stream_step_to_glb

    child = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    parent_a = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    parent_b = ((10.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    block = _idt_block(child, parent_a)
    block += (
        _axis2_lines(300, *child)
        + _axis2_lines(310, *parent_b)
        + "#388=ITEM_DEFINED_TRANSFORMATION('','',#300,#310);\n"
        "#389=(REPRESENTATION_RELATIONSHIP('','',#172,#179)"
        "REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#388)SHAPE_REPRESENTATION_RELATIONSHIP());\n"
        "#390=CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#389,#187);\n"
    )
    path = tmp_path / "multi.step"
    path.write_text(_render(block))
    glb = tmp_path / "multi.glb"
    stream_step_to_glb(path, glb, tolerant=True)

    raw = glb.read_bytes()
    jlen = struct.unpack("<I", raw[12:16])[0]
    meta = json.loads(raw[20 : 20 + jlen])["scenes"][0]["extras"]["id_hierarchy"]
    by_name = {}
    for nid, (name, pid) in meta.items():
        by_name.setdefault(name, []).append((nid, pid))

    assert "box/2" in by_name, f"missing second instance: {meta}"
    # 'box' appears twice: the product group node and the first instance.
    assert len(by_name["box"]) == 2, f"expected group + instance named 'box': {meta}"
    (a_id, _a_parent) = by_name["a"][0]
    # Both instances hang under the SAME 'box' group node, which hangs under 'a'.
    box_group = [nid for nid, pid in by_name["box"] if str(pid) == str(a_id)]
    assert box_group, f"'box' group not parented under 'a': {meta}"
    leaf_parents = {str(pid) for nid, pid in by_name["box"] + by_name["box/2"] if str(nid) not in box_group}
    assert leaf_parents == {str(box_group[0])}, f"instances not grouped under 'box': {meta}"

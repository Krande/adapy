"""A surface of the mesh as a standalone interface mesh, and its .vtu, .npz and .csv files."""

from __future__ import annotations

import base64
import struct
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import ada
from ada.fem import Elem, FemSet, Surface, interface_mesh

from .theory_models import closed_box

OUTWARD = [[0, 0, -1], [0, 0, 1], [0, -1, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0]]


def test_a_shell_set_is_its_faces_with_the_fe_node_ids():
    _, p, fs = closed_box(2.0, 3.0, 4.0)
    mesh = interface_mesh(fs)
    assert mesh.name == "box" and mesh.num_faces == 6 and len(mesh.points) == 8
    assert sorted(mesh.node_ids.tolist()) == list(range(1, 9))
    for nid, pt in zip(mesh.node_ids, mesh.points):
        assert np.allclose(pt, p.fem.nodes.from_id(int(nid)).p)
    assert mesh.element_ids.tolist() == [1, 2, 3, 4, 5, 6]
    assert np.allclose(mesh.face_normals, OUTWARD)
    assert mesh.face_areas.tolist() == pytest.approx([6, 6, 8, 12, 8, 12])
    # Each face refers to the nodes of its element, in its element's order.
    for face, el in zip(mesh.faces, fs.members):
        assert mesh.node_ids[face].tolist() == [n.id for n in el.nodes]
    assert mesh.triangles().shape == (12, 3)
    vn = mesh.vertex_normals()
    assert np.allclose(np.linalg.norm(vn, axis=1), 1.0)
    # The corner at the origin: the faces x = 0, y = 0 and z = 0 around it, weighted by their areas 12, 8 and 6.
    assert np.allclose(vn[0], -np.array([12, 8, 6]) / np.linalg.norm([12, 8, 6]))


def test_the_negative_face_turns_the_normals_round():
    _, _, fs = closed_box()
    mesh = interface_mesh(Surface("neg", "ELEMENT", fs, el_face_index=-1))
    assert np.allclose(mesh.face_normals, -np.array(OUTWARD))


def test_a_solid_set_is_its_outside():
    p = ada.Part("s")
    pts = [(x, y, z) for z in (0, 1) for y in (0, 1) for x in (0, 1, 2)]
    nodes = {q: p.fem.nodes.add(ada.Node(q, i + 1)) for i, q in enumerate(pts)}

    def hex_at(x0):
        corners = [(x0, 0, 0), (x0 + 1, 0, 0), (x0 + 1, 1, 0), (x0, 1, 0)]
        return [nodes[c] for c in corners] + [nodes[(c[0], c[1], 1)] for c in corners]

    els = [p.fem.add_elem(Elem(i + 1, hex_at(x0), "HEXAHEDRON")) for i, x0 in enumerate((0, 1))]
    mesh = interface_mesh(p.fem.add_set(FemSet("block", els, "elset")))
    # Two bricks side by side: 10 outer faces, the shared one left out.
    assert mesh.num_faces == 10 and len(mesh.points) == 12
    assert mesh.face_areas.sum() == pytest.approx(2 * (2 + 2 + 1))
    centroids = mesh.face_centroids
    outward = np.einsum("ij,ij->i", mesh.face_normals, centroids - [1.0, 0.5, 0.5])
    assert (outward > 0).all()


def _array(piece, path, name):
    for da in piece.find(path).iter("DataArray"):
        if da.get("Name") == name:
            return da
    raise KeyError(name)


def _decode(da) -> np.ndarray:
    fmt = {"Float64": "d", "Int64": "q", "UInt8": "B"}[da.get("type")]
    raw = base64.b64decode(da.text)
    (nbytes,) = struct.unpack("I", raw[:4])
    return np.array(struct.unpack(f"{nbytes // struct.calcsize(fmt)}{fmt}", raw[4 : 4 + nbytes]))


def test_the_vtu_npz_and_csv_files(tmp_path):
    _, _, fs = closed_box(2.0, 3.0, 4.0)
    mesh = interface_mesh(fs)

    piece = ET.parse(mesh.to_vtu(tmp_path / "box.vtu")).getroot().find("UnstructuredGrid/Piece")
    assert (piece.get("NumberOfPoints"), piece.get("NumberOfCells")) == ("8", "6")
    points = _decode(piece.find("Points/DataArray")).reshape(-1, 3)
    assert np.array_equal(points, mesh.points)
    assert np.array_equal(_decode(_array(piece, "Cells", "connectivity")), mesh.connectivity)
    assert _decode(_array(piece, "Cells", "offsets")).tolist() == [4, 8, 12, 16, 20, 24]
    assert set(_decode(_array(piece, "Cells", "types")).tolist()) == {9}
    assert np.array_equal(_decode(_array(piece, "PointData", "node_id")), mesh.node_ids)
    assert np.allclose(_decode(_array(piece, "CellData", "normal")).reshape(-1, 3), OUTWARD)

    data = np.load(mesh.to_npz(tmp_path / "box.npz"))
    assert str(data["name"]) == "box"
    for key in ("points", "node_ids", "connectivity", "offsets", "element_ids", "face_normals", "face_areas"):
        assert np.array_equal(data[key], getattr(mesh, key))

    nodes_csv, faces_csv = mesh.to_csv(tmp_path)
    nodes = np.genfromtxt(nodes_csv, delimiter=",", names=True)
    assert np.array_equal(nodes["node_id"].astype(int), mesh.node_ids)
    assert np.array_equal(np.column_stack([nodes["x"], nodes["y"], nodes["z"]]), mesh.points)
    faces = faces_csv.read_text().splitlines()
    assert faces[0] == "index,element_id,nx,ny,nz,area,v0,v1,v2,v3"
    assert faces[1] == "0,1,0.0,0.0,-1.0,6.0," + ",".join(str(v) for v in mesh.faces[0])


def test_a_mixed_mesh_of_triangles_and_quads():
    p = ada.Part("m")
    n = [
        p.fem.nodes.add(ada.Node(q, i + 1))
        for i, q in enumerate([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0)])
    ]
    q = p.fem.add_elem(Elem(1, n[:4], "QUAD"))
    t = p.fem.add_elem(Elem(2, [n[1], n[4], n[2]], "TRIANGLE"))
    mesh = interface_mesh(p.fem.add_set(FemSet("patch", [q, t], "elset")))
    assert np.diff(mesh.offsets).tolist() == [4, 3]
    assert mesh.face_areas.tolist() == pytest.approx([1.0, 0.5])
    assert mesh.triangles().shape == (3, 3)


def test_a_node_set_is_no_interface():
    _, p, fs = closed_box()
    with pytest.raises(ValueError, match="node set"):
        interface_mesh(p.fem.add_set(FemSet("pts", [p.fem.nodes.from_id(1)], "nset")))

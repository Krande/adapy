"""A surface of the FE mesh as a standalone surface mesh, for coupling to another solver.

A partitioned co-simulation -- structure against flow, say, through a coupling library such as preCICE -- exchanges
data on the interface between the two models: the structural solver hands over the displacements of a surface, the
other solver hands back the loads on it. The coupling library needs that surface as a mesh of its own: the vertex
coordinates, the faces between them, and for each vertex the structural node it is, so that what comes back can be
applied to the right node.

:func:`interface_mesh` builds that mesh from a :class:`~ada.fem.Surface` or a :class:`~ada.fem.FemSet` -- the faces
of shell (or membrane) elements, or the faces of solid elements. :class:`InterfaceMesh` writes it to ``.vtu``, to a
``.npz`` and to a pair of ``.csv`` files.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np

from .sets import FemSet
from .surfaces import Surface

if TYPE_CHECKING:
    from .surfaces import SurfaceFacet

#: What an interface is taken from.
InterfaceRegion = Union[Surface, FemSet]


@dataclass
class InterfaceMesh:
    """A surface mesh: vertices, faces, and the FE node each vertex is.

    Faces are stored flat, VTK style: face ``i`` is ``connectivity[offsets[i]:offsets[i + 1]]``, indices into
    ``points``. Triangles and quadrilaterals may be mixed. Normals are unit vectors in the surface's own orientation
    (a shell's positive face, out of a solid).

    :param name: the region's name.
    :param points: ``(N, 3)`` vertex coordinates.
    :param node_ids: ``(N,)`` the FE node id of each vertex.
    :param connectivity: the faces' vertex indices, one face after the other.
    :param offsets: ``(F + 1,)`` where each face starts in ``connectivity``; the last entry is its length.
    :param element_ids: ``(F,)`` the element each face is a face of.
    :param face_normals: ``(F, 3)`` unit normal of each face (of its corner polygon, by Newell's method).
    :param face_areas: ``(F,)`` area of each face.
    """

    name: str
    points: np.ndarray
    node_ids: np.ndarray
    connectivity: np.ndarray
    offsets: np.ndarray
    element_ids: np.ndarray
    face_normals: np.ndarray
    face_areas: np.ndarray

    @property
    def num_faces(self) -> int:
        return len(self.offsets) - 1

    @property
    def faces(self) -> list[np.ndarray]:
        """Each face's vertex indices."""
        return [self.connectivity[self.offsets[i] : self.offsets[i + 1]] for i in range(self.num_faces)]

    @property
    def face_centroids(self) -> np.ndarray:
        return np.array([self.points[f].mean(axis=0) for f in self.faces]).reshape(-1, 3)

    def triangles(self) -> np.ndarray:
        """``(T, 3)`` vertex indices, each quadrilateral split along its 0-2 diagonal."""
        tris = []
        for f in self.faces:
            if len(f) == 3:
                tris.append(f)
            else:
                tris += [f[[0, 1, 2]], f[[0, 2, 3]]]
        return np.array(tris, dtype=np.int64).reshape(-1, 3)

    def vertex_normals(self) -> np.ndarray:
        """``(N, 3)`` unit normal at each vertex: the area-weighted mean of the normals of the faces around it."""
        acc = np.zeros_like(self.points)
        for f, n, a in zip(self.faces, self.face_normals, self.face_areas):
            acc[f] += n * a
        length = np.linalg.norm(acc, axis=1)
        length[length == 0.0] = 1.0
        return acc / length[:, None]

    # ---- writers -----------------------------------------------------------------------------------------------
    def to_vtu(self, path: str | pathlib.Path) -> pathlib.Path:
        """Write a VTK unstructured grid of polygons: double-precision points, ``node_id`` point data, and
        ``element_id``, ``normal`` and ``area`` cell data."""
        from .formats.vtu.write import array_to_binary

        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        vtk_type = {3: 5, 4: 9}  # VTK_TRIANGLE, VTK_QUAD
        sizes = np.diff(self.offsets)
        types = [vtk_type[int(s)] for s in sizes]

        root = ET.Element("VTKFile", type="UnstructuredGrid", version="1.0", byte_order="LittleEndian")
        piece = ET.SubElement(
            ET.SubElement(root, "UnstructuredGrid"),
            "Piece",
            NumberOfPoints=str(len(self.points)),
            NumberOfCells=str(self.num_faces),
        )

        def array(parent, name, dtype_vtk, fmt, values, components=1):
            attrs = dict(type=dtype_vtk, format="binary")
            if name is not None:
                attrs["Name"] = name
            if components != 1:
                attrs["NumberOfComponents"] = str(components)
            ET.SubElement(parent, "DataArray", **attrs).text = array_to_binary(np.asarray(values).ravel().tolist(), fmt)

        array(ET.SubElement(piece, "Points"), None, "Float64", "d", self.points, 3)
        cells = ET.SubElement(piece, "Cells")
        array(cells, "connectivity", "Int64", "q", self.connectivity)
        array(cells, "offsets", "Int64", "q", self.offsets[1:])
        array(cells, "types", "UInt8", "B", types)
        array(ET.SubElement(piece, "PointData"), "node_id", "Int64", "q", self.node_ids)
        cell_data = ET.SubElement(piece, "CellData")
        array(cell_data, "element_id", "Int64", "q", self.element_ids)
        array(cell_data, "normal", "Float64", "d", self.face_normals, 3)
        array(cell_data, "area", "Float64", "d", self.face_areas)

        with open(path, "wb") as f:
            f.write(b'<?xml version="1.0"?>\n')
            ET.ElementTree(root).write(f)
        return path

    def to_npz(self, path: str | pathlib.Path) -> pathlib.Path:
        """Write every array of the mesh to one ``.npz`` (``numpy.load`` reads it back)."""
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path,
            name=np.array(self.name),
            points=self.points,
            node_ids=self.node_ids,
            connectivity=self.connectivity,
            offsets=self.offsets,
            element_ids=self.element_ids,
            face_normals=self.face_normals,
            face_areas=self.face_areas,
        )
        return path

    def to_csv(self, directory: str | pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        """Write ``<name>_nodes.csv`` (``index, node_id, x, y, z``) and ``<name>_faces.csv`` (``index, element_id, nx,
        ny, nz, area, v0, v1, v2[, v3]``; ``v*`` index the nodes file, and a triangle leaves ``v3`` empty)."""
        directory = pathlib.Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        nodes_path = directory / f"{self.name}_nodes.csv"
        faces_path = directory / f"{self.name}_faces.csv"
        with open(nodes_path, "w") as f:
            f.write("index,node_id,x,y,z\n")
            for i, (nid, p) in enumerate(zip(self.node_ids, self.points)):
                f.write(f"{i},{int(nid)},{_num(p[0])},{_num(p[1])},{_num(p[2])}\n")
        with open(faces_path, "w") as f:
            f.write("index,element_id,nx,ny,nz,area,v0,v1,v2,v3\n")
            for i, (face, eid, n, a) in enumerate(
                zip(self.faces, self.element_ids, self.face_normals, self.face_areas)
            ):
                verts = [str(int(v)) for v in face] + [""] * (4 - len(face))
                f.write(f"{i},{int(eid)},{_num(n[0])},{_num(n[1])},{_num(n[2])},{_num(a)},{','.join(verts)}\n")
        return nodes_path, faces_path


def interface_mesh(region: InterfaceRegion, name: str | None = None) -> InterfaceMesh:
    """The surface mesh of ``region``, its corner nodes only.

    ``region`` is a :class:`~ada.fem.Surface` (shell faces ``SPOS`` / ``SNEG``, solid faces ``S1`` ...), or a
    :class:`~ada.fem.FemSet` of elements: a set of shells gives their positive faces, a set of solids the faces on
    the set's outside (its free faces). Mid-side nodes are left out, so a second-order element contributes its corner
    polygon.
    """
    from .cavities import _is_reversed
    from .shapes.definitions import ShellShapes, SolidShapes
    from .surfaces import _group_facets, surface_facets

    if isinstance(region, FemSet):
        if region.type != FemSet.TYPES.ELSET:
            raise ValueError(f"an interface is made of element faces; {region.name!r} is a node set")
        solids = [m for m in region.members if isinstance(m.type, SolidShapes)]
        # A set names no side; a set of solids is read as its free faces, which is the surface it has.
        side = "" if solids else None
        facets = [f for f, _ in _group_facets(region.name, region.members, side) if f is not None]
    else:
        facets = surface_facets(region)

    if not facets:
        raise ValueError(f"{getattr(region, 'name', region)!r} has no element faces")

    index: dict[int, int] = {}
    points, node_ids, conn, offsets, el_ids, normals, areas = [], [], [], [0], [], [], []
    for facet in facets:
        facet: SurfaceFacet
        if not isinstance(facet.shape, ShellShapes):
            raise ValueError(f"a facet of element {facet.element.id} is an edge; an interface is made of faces")
        n_corners = 3 if facet.shape in (ShellShapes.TRI, ShellShapes.TRI6, ShellShapes.TRI7) else 4
        corners = list(facet.nodes[:n_corners])
        if _is_reversed(facet):
            corners = corners[::-1]
        for node in corners:
            if node.id not in index:
                index[node.id] = len(points)
                points.append(np.asarray(node.p, dtype=float))
                node_ids.append(int(node.id))
            conn.append(index[node.id])
        offsets.append(len(conn))
        el_ids.append(int(facet.element.id))
        normal, area = _newell(np.array([n.p for n in corners], dtype=float))
        normals.append(normal)
        areas.append(area)

    return InterfaceMesh(
        name=name or getattr(region, "name", "interface"),
        points=np.array(points, dtype=float).reshape(-1, 3),
        node_ids=np.array(node_ids, dtype=np.int64),
        connectivity=np.array(conn, dtype=np.int64),
        offsets=np.array(offsets, dtype=np.int64),
        element_ids=np.array(el_ids, dtype=np.int64),
        face_normals=np.array(normals, dtype=float).reshape(-1, 3),
        face_areas=np.array(areas, dtype=float),
    )


def _num(value) -> str:
    """A float as the shortest text that reads back to the same double."""
    return repr(float(value))


def _newell(pts: np.ndarray) -> tuple[np.ndarray, float]:
    """Unit normal and area of a polygon (Newell's method: exact for a flat polygon, the mean plane of a warped one)."""
    vec = 0.5 * np.sum(np.cross(pts, np.roll(pts, -1, axis=0)), axis=0)
    area = float(np.linalg.norm(vec))
    return (vec / area if area > 0.0 else vec), area

import numpy as np
import pygfx as gfx
import pylinalg as la
import trimesh
from pygfx import (
    Geometry,
    Line,
    LineSegmentMaterial,
    Mesh,
    MeshBasicMaterial,
    cone_geometry,
)
from pygfx.utils import Color

from ada.config import logger
from ada.visit.colors import Color as AdaColor
from ada.visit.gltf.meshes import MeshStore

DTYPE = "f4"


class AxesHelper(Line):
    """A WorldObject to indicate the scene's axes.

    Generates three arrows starting at the local origin and pointing into the
    direction of the local x, y, and z-axis respectively. Each arrow is colored
    to represent the respective axis. In particular, the x-axis arrow is blue,
    the y-axis arrow is green, and the z-axis arrow is red.

    Parameters
    ----------
    size : float
        The length of the lines in local space.
    thickness : float
        The thickness of the lines in (onscreen) pixels.

    """

    def __init__(self, size=1.0, thickness=2):
        line_positions = np.array(
            [
                [0, 0, 0],
                [1, 0, 0],
                [0, 0, 0],
                [0, 0, -1],
                [0, 0, 0],
                [0, 1, 0],
            ],
            dtype=DTYPE,
        )

        colors = np.array(
            [
                [1, 0, 0, 1],
                [1, 0, 0, 1],  # x is red
                [0, 1, 0, 1],
                [0, 1, 0, 1],  # y is green
                [0, 0, 1, 1],
                [0, 0, 1, 1],  # z is blue
            ],
            dtype=DTYPE,
        )

        arrow_radius = size * 0.1
        # the radius of the cone is the thickness, so that the arrow is twice as wide
        # as the line it sits on.
        # we want the arrow head to maintain the proportions of a equilateral triangle
        # when viewed from the side, so the desired height can be computed
        # by multiplying the radius by sqrt(3)
        arrow_size = np.sqrt(3) * arrow_radius
        cone = cone_geometry(radius=arrow_radius, height=arrow_size)

        line_size = np.max([0.1, size - arrow_size])  # ensure > 0.0
        line_positions *= line_size

        geometry = Geometry(positions=line_positions, colors=colors)
        material = LineSegmentMaterial(thickness=thickness, aa=True, color_mode="vertex")

        super().__init__(geometry, material)

        for pos, color in zip(line_positions[1::2], colors[1::2]):
            material = MeshBasicMaterial(color=color)
            arrow_head = Mesh(cone, material)
            arrow_head.local.position = pos
            # offset by half of height since the cones
            # are centered around the origin
            arrow_head.local.position = arrow_head.local.position + arrow_size / 2 * la.vec_normalize(pos)
            arrow_head.local.rotation = la.quat_from_vecs((0, 0, 1), la.vec_normalize(pos))
            self.add(arrow_head)

    def set_colors(self, x, y, z):
        """Update arrow colors.

        Parameters
        ----------
        x : int, float, str, tuple
            The color of the x arrow. This is a either a single int or float
            (gray), a 4-tuple ``(r,g,b,a)`` of ints or floats, or a hex-coded
            color string in one of the following formats: ``#RGB``, ``#RGBA``,
            ``#RRGGBB``, ``#RRGGBBAA``.
        y : int, float, str, tuple
            The color of the x arrow. This is a either a single int or float
            (gray), a 4-tuple ``(r,g,b,a)`` of ints or floats, or a hex-coded
            color string in one of the following formats: ``#RGB``, ``#RGBA``,
            ``#RRGGBB``, ``#RRGGBBAA``.
        z : int, float, str, tuple
            The color of the x arrow. This is a either a single int or float
            (gray), a 4-tuple ``(r,g,b,a)`` of ints or floats, or a hex-coded
            color string in one of the following formats: ``#RGB``, ``#RGBA``,
            ``#RRGGBB``, ``#RRGGBBAA``.

        """

        x, y, z = Color(x), Color(y), Color(z)
        # update lines
        self._geometry.colors.data[0] = x
        self._geometry.colors.data[1] = x
        self._geometry.colors.data[2] = y
        self._geometry.colors.data[3] = y
        self._geometry.colors.data[4] = z
        self._geometry.colors.data[5] = z
        self._geometry.colors.update_range(0, self._geometry.colors.nitems)
        # update arrow heads
        for arrow, color in zip(self.children, [x, y, z]):
            arrow.material.color = color


class GridHelper(Line):
    """A WorldObject that shows a grid-shaped wireframe.

    The generated grid will be in the xz-plane centered at the origin. To
    position the grid, manipulate its parent's position, rotation, etc.

    Parameters
    ----------
    size : float
        The size of the wireframe in the direction of the x-, and y-axis.
    divisions : int
        The number of (evenly spaced) divisions to perform along each axis.
    color1 : int, float, str, tuple
        The color of the center lines. This is a either a single int or float
        (gray), a 4-tuple ``(r,g,b,a)`` of ints or floats, or a hex-coded color
        string in one of the following formats: ``#RGB``, ``#RGBA``,
        ``#RRGGBB``, ``#RRGGBBAA``.
    color2 : int, float, str, tuple
        The color of non-center lines. This is a either a single int or float
        (gray), a 4-tuple ``(r,g,b,a)`` of ints or floats, or a hex-coded color
        string in one of the following formats: ``#RGB``, ``#RGBA``,
        ``#RRGGBB``, ``#RRGGBBAA``.
    thickness : int
        The thickness in screen units (pixels).

    """

    def __init__(
        self,
        size=10.0,
        divisions=10,
        color1=(0.35, 0.35, 0.35, 1),
        color2=(0.1, 0.1, 0.1, 1),
        thickness=1,
    ):
        assert isinstance(divisions, int)
        assert size > 0.0

        half_size = size / 2
        n_lines = divisions + 1
        x = np.linspace(-half_size, half_size, num=n_lines, dtype=DTYPE)

        # the grid is made up of 2 * n_lines line segments
        # where each line has two endpoints (2, 3)
        positions = np.zeros((2, n_lines, 2, 3), dtype=DTYPE)
        positions[0, ..., 0] = x[:, None]
        positions[0, ..., 2] = [[-half_size, half_size]]
        positions[1, ..., 0] = [[-half_size, half_size]]
        positions[1, ..., 2] = x[:, None]

        # color1 for the center lines, color2 for the rest
        colors = np.empty((2, n_lines, 2, 4), dtype=DTYPE)
        colors[..., :] = Color(color2)
        colors[:, n_lines // 2, :, :] = Color(color1)

        geometry = Geometry(positions=positions.reshape((-1, 3)), colors=colors.reshape((-1, 4)))
        material = LineSegmentMaterial(vertex_colors=True, thickness=thickness, aa=True)

        super().__init__(geometry, material)


def tri_mat_to_gfx_mat(
    tri_mat: trimesh.visual.material.PBRMaterial,
) -> gfx.MeshPhongMaterial | gfx.MeshBasicMaterial:
    if tri_mat.baseColorFactor is None:
        color = gfx.Color(1, 1, 1)
    else:
        color = gfx.Color(*[x / 255 for x in tri_mat.baseColorFactor[:3]])

    return gfx.MeshPhongMaterial(color=color, flat_shading=True)


def geometry_from_mesh(
    mesh: trimesh.Trimesh | trimesh.path.Path3D | MeshStore,
) -> gfx.Geometry:
    """Convert a Trimesh geometry object to pygfx geometry."""

    if isinstance(mesh, MeshStore):
        geom = gfx.Geometry(
            positions=np.ascontiguousarray(mesh.get_position3(), dtype="f4"),
            indices=np.ascontiguousarray(mesh.get_indices3(), dtype="i4"),
        )
    elif isinstance(mesh, trimesh.path.Path3D):
        vertices = np.ascontiguousarray(mesh.vertices, dtype="f4")
        indices = np.ascontiguousarray(mesh.vertex_nodes, dtype="i4")
        geom = gfx.Geometry(positions=vertices, indices=indices)
    elif isinstance(mesh, (trimesh.points.PointCloud)):
        geom = gfx.Geometry(positions=np.ascontiguousarray(mesh.vertices, dtype="f4"))
    else:
        geom = gfx.Geometry(
            positions=np.ascontiguousarray(mesh.vertices, dtype="f4"),
            indices=np.ascontiguousarray(mesh.faces, dtype="i4"),
        )

    return geom


def gfx_mesh_from_mesh(
    mesh: trimesh.Trimesh | trimesh.path.Path3D | MeshStore, material=None
) -> gfx.Mesh | gfx.Points | gfx.Line:
    default_point_color = AdaColor.from_str("black")
    default_line_color = AdaColor.from_str("gray")

    if isinstance(mesh, MeshStore):
        mat = tri_mat_to_gfx_mat(mesh.visual.material)
        geom = gfx.Geometry(
            positions=np.ascontiguousarray(mesh.get_position3(), dtype="f4"),
            indices=np.ascontiguousarray(mesh.get_indices3(), dtype="i4"),
        )
        mesh = gfx.Mesh(geom, material=mat)
    elif isinstance(mesh, trimesh.points.PointCloud):
        geom = gfx.Geometry(positions=np.ascontiguousarray(mesh.vertices, dtype="f4"))
        # if hasattr(mesh, "visual"):
        #     mat = gfx.PointsMaterial(size=10, color=mesh.visual.main_color)
        # else:
        mat = gfx.PointsMaterial(size=10, color=Color(*default_point_color.rgb))
        mesh = gfx.Points(geom, material=mat)
    elif isinstance(mesh, trimesh.path.Path3D):
        indices = np.ascontiguousarray(mesh.vertex_nodes, dtype="i4")
        positions = np.zeros((indices.shape[0] * 2, 3), dtype="f4")
        i = 0
        for p1, p2 in mesh.vertex_nodes:
            positions[i] = mesh.vertices[p1]
            positions[i + 1] = mesh.vertices[p2]
            i += 2
        geom = gfx.Geometry(positions=positions)
        if hasattr(mesh, "visual"):
            mat = gfx.LineSegmentMaterial(thickness=3, color=mesh.visual.main_color)
        else:
            mat = gfx.LineSegmentMaterial(thickness=3, color=Color(*default_line_color.rgb))
        mesh = gfx.Line(geom, material=mat)
    else:
        geom = gfx.Geometry(
            positions=np.ascontiguousarray(mesh.vertices, dtype="f4"),
            indices=np.ascontiguousarray(mesh.faces, dtype="i4"),
        )
        if material is None:
            # This seems to have broken with newer versions of pygfx
            if hasattr(mesh.visual, "material"):
                mat = tri_mat_to_gfx_mat(mesh.visual.material)
            else:
                logger.warning(
                    "No material found for mesh, using default color. Maybe related to changes in trimesh>4?"
                )
                color = mesh.visual.main_color
                mat = gfx.MeshPhongMaterial(color=color, flat_shading=True)
        else:
            mat = material

        mesh = gfx.Mesh(geom, material=mat)

    return mesh

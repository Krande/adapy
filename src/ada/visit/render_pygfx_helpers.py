import numpy as np
import pygfx as gfx
import trimesh
from pygfx import (
    Geometry,
    Line,
    LineSegmentMaterial,
    Mesh,
    MeshBasicMaterial,
    cone_geometry,
)
from pygfx.linalg import Vector3
from pygfx.utils import Color

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
        material = LineSegmentMaterial(vertex_colors=True, thickness=thickness, aa=True)

        super().__init__(geometry, material)

        for pos, color in zip(line_positions[1::2], colors[1::2]):
            material = MeshBasicMaterial(color=color)
            arrow_head = Mesh(cone, material)
            arrow_head.position = Vector3(*pos)
            # offset by half of height since the cones
            # are centered around the origin
            arrow_head.position.add_scaled_vector(Vector3(*pos).normalize(), arrow_size / 2)
            arrow_head.rotation.set_from_unit_vectors(
                Vector3(0, 0, 1),
                Vector3(*pos).normalize(),
            )
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


def tri_mat_to_gfx_mat(tri_mat: trimesh.visual.material.PBRMaterial) -> gfx.MeshPhongMaterial | gfx.MeshBasicMaterial:
    color = gfx.Color(*[x / 255 for x in tri_mat.baseColorFactor[:3]])

    return gfx.MeshPhongMaterial(color=color, flat_shading=True)


def geometry_from_mesh(mesh: trimesh.Trimesh | MeshStore) -> gfx.Geometry:
    """Convert a Trimesh geometry object to pygfx geometry."""

    if isinstance(mesh, MeshStore):
        kwargs = dict(
            positions=np.ascontiguousarray(mesh.get_position3(), dtype="f4"),
            indices=np.ascontiguousarray(mesh.get_indices3(), dtype="i4"),
        )
    else:
        kwargs = dict(
            positions=np.ascontiguousarray(mesh.vertices, dtype="f4"),
            indices=np.ascontiguousarray(mesh.faces, dtype="i4"),
        )
        if mesh.visual.kind == "texture" and mesh.visual.uv is not None and len(mesh.visual.uv) > 0:
            # convert the uv coordinates from opengl to wgpu conventions.
            # wgpu uses the D3D and Metal coordinate systems.
            # the coordinate origin is in the upper left corner, while the opengl coordinate
            # origin is in the lower left corner.
            # trimesh loads textures according to the opengl coordinate system.
            wgpu_uv = mesh.visual.uv * np.array([1, -1]) + np.array([0, 1])  # uv.y = 1 - uv.y
            kwargs["texcoords"] = np.ascontiguousarray(wgpu_uv, dtype="f4")
        elif mesh.visual.kind == "vertex":
            kwargs["colors"] = np.ascontiguousarray(mesh.visual.vertex_colors, dtype="f4")

    return gfx.Geometry(**kwargs)

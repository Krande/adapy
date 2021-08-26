import numpy as np
from pythreejs import (
    BufferAttribute,
    BufferGeometry,
    LineBasicMaterial,
    LineSegments,
    Mesh,
    MeshBasicMaterial,
    MeshPhongMaterial,
    Points,
    PointsMaterial,
)


def format_color(r, g, b):
    return "#%02x%02x%02x" % (r, g, b)


def edges_to_mesh(name, np_edge_vertices, np_edge_indices, edge_color, linewidth=1):
    """

    :param name:
    :param np_edge_vertices:
    :param np_edge_indices:
    :param edge_color:
    :param linewidth:
    :return:
    :rtype: pythreejs.objects.LineSegments_autogen.LineSegments
    """
    edge_geometry = BufferGeometry(
        attributes={
            "position": BufferAttribute(np_edge_vertices, normalized=False),
            "index": BufferAttribute(np_edge_indices, normalized=False),
        }
    )
    edge_material = LineBasicMaterial(color=format_color(*edge_color), linewidth=linewidth)

    edge_geom = LineSegments(
        geometry=edge_geometry,
        material=edge_material,
        type="LinePieces",
        name=name,
    )

    return edge_geom


def vertices_to_mesh(name, vertices, vertex_color, vertex_width=5):
    """

    :param name:
    :param vertices:
    :param vertex_color: RGB tuple (r, g, b)
    :param vertex_width:
    :return:
    :rtype: pythreejs.objects.Points_autogen.Points
    """
    vertices = np.array(vertices, dtype=np.float32)
    attributes = {"position": BufferAttribute(vertices, normalized=False)}
    mat = PointsMaterial(color=format_color(*vertex_color), sizeAttenuation=False, size=vertex_width)
    geom = BufferGeometry(attributes=attributes)
    points = Points(geometry=geom, material=mat, name=name)
    return points


def faces_to_mesh(name, vertices, faces, colors, opacity=None):
    """

    :param name:
    :param vertices:
    :param faces:
    :param colors:
    :param opacity:
    :return:
    """
    geometry = BufferGeometry(
        attributes=dict(
            position=BufferAttribute(vertices, normalized=False),
            index=BufferAttribute(faces, normalized=False),
            color=BufferAttribute(colors),
        )
    )

    mat_atts = dict(vertexColors="VertexColors", side="DoubleSide")
    if opacity is not None:
        mat_atts["opacity"] = opacity
        mat_atts["transparent"] = True

    material = MeshBasicMaterial(**mat_atts)
    mesh = Mesh(
        name=name,
        geometry=geometry,
        material=material,
    )
    return mesh


def animate_mesh(mesh, norm_displ_verts, displ_time, displ_magn):
    from pythreejs import (
        AnimationAction,
        AnimationClip,
        AnimationMixer,
        NumberKeyframeTrack,
    )

    mesh.morphAttributes = {
        "position": [
            BufferAttribute(norm_displ_verts),
        ]
    }

    morphed_mesh = Mesh(mesh, MeshPhongMaterial(color="#ff3333", shininess=150, morphTargets=True))

    pill_track = NumberKeyframeTrack(name=".morphTargetInfluences[0]", times=displ_time, values=displ_magn)
    pill_clip = AnimationClip(tracks=[pill_track])
    pill_action = AnimationAction(AnimationMixer(morphed_mesh), pill_clip, morphed_mesh)

    return pill_action

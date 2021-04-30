import numpy as np
from IPython.display import display
from pythreejs import BufferAttribute, BufferGeometry, Mesh, MeshLambertMaterial


def make_geom(vertices, faces, colors):
    geometry = BufferGeometry(
        attributes=dict(
            position=BufferAttribute(vertices, normalized=False),
            index=BufferAttribute(faces, normalized=False),
            color=BufferAttribute(colors),
        )
    )

    mesh = Mesh(
        geometry=geometry,
        material=MeshLambertMaterial(vertexColors="VertexColors"),
        # position=[-0.5, -0.5, -0.5],  # Center the cube
    )
    return mesh


def get_mesh_faces(fem):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :return:
    :rtype: list
    """
    faceids = []
    for el in fem.elements.elements:
        for f in el.shape.faces:
            # Convert to indices, not id
            faceids += [[e.id - 1 for e in f]]
    return faceids


def render_mesh(vertices, faces, colors):
    from ipywidgets import HBox, VBox

    from .renderer import MyRenderer

    mesh = make_geom(vertices, faces, colors)

    renderer = MyRenderer()
    renderer._displayed_pickable_objects.add(mesh)
    renderer.build_display()
    display(HBox([VBox([HBox(renderer._controls), renderer._renderer]), renderer.html]))
    #
    # cCube = PerspectiveCamera(
    #     position=[3, 3, 3], fov=20, children=[DirectionalLight(color="#ffffff", position=[-3, 5, 1], intensity=0.5)]
    # )
    # sceneCube = Scene(children=[mesh, cCube, AmbientLight(color="#dddddd")])
    #
    # rendererCube = Renderer(
    #     camera=cCube,
    #     background="black",
    #     background_opacity=1,
    #     scene=sceneCube,
    #     controls=[OrbitControls(controlling=cCube)],
    # )
    #
    # display(rendererCube)


def viz_fem(fem, mesh, data_type):
    """

    :param fem:
    :type fem: ada.fem.FEM
    :param mesh:
    :type mesh: meshio.
    :param data_type:
    :type data_type:
    :return:
    :rtype:
    """
    u = np.asarray(mesh.point_data[data_type], dtype="float32")

    def magnitude(u_):
        return np.sqrt(u_[0] ** 2 + u_[1] ** 2 + u_[2] ** 2)

    vertices = mesh.points
    faces = np.asarray(get_mesh_faces(fem), dtype="uint16").ravel()

    res = [magnitude(u_) for u_ in u]
    max_r = max(res)
    res_norm_col = np.asarray([(x / max_r, 0, 0) for x in res], dtype="float32")

    render_mesh(vertices, faces, res_norm_col)

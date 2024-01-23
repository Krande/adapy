from pythreejs import (
    BufferAttribute,
    BufferGeometry,
    Mesh,
    MeshBasicMaterial,
)


def faces_to_mesh(name, vertices, faces, colors, opacity=None):
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


def create_material(color, transparent=False, opacity=1.0):
    from OCC.Display.WebGl.jupyter_renderer import CustomMaterial

    # material = MeshPhongMaterial()
    material = CustomMaterial("standard")
    material.color = color
    material.clipping = True
    material.side = "DoubleSide"
    material.polygonOffset = True
    material.polygonOffsetFactor = 1
    material.polygonOffsetUnits = 1
    material.transparent = transparent
    material.opacity = opacity
    material.update("metalness", 0.3)
    material.update("roughness", 0.8)
    return material

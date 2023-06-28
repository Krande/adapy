import pathlib
import trimesh

from ada.visit.render_backend import SqLiteBackend, MeshInfo
from ada.visit.render_pygfx import RendererPyGFX

glb_file = pathlib.Path(__file__).parent.parent.parent.parent / "files/gltf_files" / "boxes_merged.glb"
if not glb_file.exists():
    raise FileNotFoundError(glb_file)

render = RendererPyGFX(render_backend=SqLiteBackend())
render.add_trimesh_scene(trimesh.load(glb_file), "boxes_merged")
render.show()

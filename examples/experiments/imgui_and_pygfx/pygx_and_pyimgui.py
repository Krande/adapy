import imgui
from imgui.integrations.glfw import GlfwRenderer
import glfw
import pygfx as gfx
from wgpu.gui.glfw import WgpuCanvas

# Initialize GLFW
window = glfw.create_window(1280, 720, "PyGFX ImGui Demo", None, None)

# Initialize PyGFX with the GLFW context
canvas = WgpuCanvas(title="canvas_title", max_fps=60)
renderer = gfx.renderers.WgpuRenderer(canvas)
display = gfx.Display(canvas=canvas, renderer=renderer)

# Initialize ImGui with the GLFW context
imgui.create_context()
imgui_io = imgui.get_io()
impl_glfw = GlfwRenderer(window, attach_callbacks=False)

scene = gfx.Scene()
# ... populate your scene ...

# Main loop
while not glfw.window_should_close(window):
    glfw.poll_events()

    # Render PyGFX scene
    renderer.render(scene)

    # Render ImGui widgets
    imgui.new_frame()
    if imgui.begin_main_menu_bar():
        if imgui.begin_menu("File", True):
            clicked_quit, selected_quit = imgui.menu_item(
                "Quit", 'Ctrl+Q', False, True
            )
            if clicked_quit:
                exit(0)
            imgui.end_menu()
    imgui.end_main_menu_bar()

    # Render ImGui frame
    imgui.render()
    impl_glfw.render(imgui.get_draw_data())

# Cleanup
impl_glfw.shutdown()
imgui.destroy_context()

glfw.terminate()

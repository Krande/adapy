import ada
from ada.visualize.renderer_pythreejs import MyRenderer, SectionRenderer

is_printed = False


def dummy_display(ada_obj):
    if type(ada_obj) is ada.Section:
        sec_render = SectionRenderer()
        _, _ = sec_render.build_display(ada_obj)
    else:
        renderer = MyRenderer()
        renderer.DisplayObj(ada_obj)
        renderer.build_display()

import logging

import pytest

import ada

is_printed = False


def dummy_display_func(ada_obj):
    try:
        pass
    except ModuleNotFoundError:
        logging.error("pythreejs is not installed. Install with 'conda install pythreejs'")
        return None

    from ada.visit.plots import build_display
    from ada.visit.rendering.renderer_pythreejs import MyRenderer

    if isinstance(ada_obj, ada.Section):
        build_display(ada_obj)
    else:
        renderer = MyRenderer()
        renderer.DisplayObj(ada_obj)
        renderer.build_display()


@pytest.fixture
def dummy_display():
    return dummy_display_func

from ada.cadit.step.store import StepStore
from ada.cadit.step.write.writer import StepWriter


def test_read_step_with_colors(colored_flat_plate_step):
    store = StepStore(colored_flat_plate_step)
    sw = StepWriter("Output")
    for shp in store.iter_all_shapes(True):
        sw.add_shape(shp.shape, shp.name, rgb_color=shp.color)
    sw.export("temp/output.stp")


def test_read_assembly_step_with_colors(colored_assembly_step):
    store = StepStore(colored_assembly_step)
    sw = StepWriter("Output")
    for shp in store.iter_all_shapes(True):
        sw.add_shape(shp.shape, shp.name, rgb_color=shp.color)
    sw.export("temp/output.stp")

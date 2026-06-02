from ada.cad.doc import active_doc_backend


def _roundtrip(src, dst):
    db = active_doc_backend()
    store = db.step_reader(src)
    sw = db.step_writer()
    for shp in store.iter_all_shapes(True):
        sw.add_shape(shp.shape, shp.name, rgb_color=shp.color)
    sw.export(dst)


def test_read_step_with_colors(colored_flat_plate_step, tmp_path):
    _roundtrip(colored_flat_plate_step, tmp_path / "output.stp")


def test_read_assembly_step_with_colors(colored_assembly_step, tmp_path):
    _roundtrip(colored_assembly_step, tmp_path / "output.stp")

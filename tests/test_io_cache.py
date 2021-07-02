import unittest

from ada import Assembly
from ada.config import Settings
from ada.param_models.basic_module import SimpleStru

test_dir = Settings.test_dir / "cache"


def cache_validation(a, b):
    amats = [mat for p in a.get_all_parts_in_assembly(True) for mat in p.materials]
    bmats = [mat for p in b.get_all_parts_in_assembly(True) for mat in p.materials]
    assert len(amats) == len(bmats)

    bsecs = [sec for p in b.get_all_parts_in_assembly(True) for sec in p.sections]
    asecs = [sec for p in a.get_all_parts_in_assembly(True) for sec in p.sections]
    assert len(asecs) == len(bsecs)

    bbeams = [bm for p in b.get_all_parts_in_assembly(True) for bm in p.beams]
    abeams = [bm for p in a.get_all_parts_in_assembly(True) for bm in p.beams]
    assert len(abeams) == len(bbeams)

    for nA, nB in zip(a.fem.nodes, b.fem.nodes):
        assert nA == nB

    for nA, nB in zip(a.fem.elements, b.fem.elements):
        assert nA == nB

    print(a)
    print(b)


class MyTestCase(unittest.TestCase):
    def test_something(self):

        model_name = "ParamAssembly"

        a = Assembly(model_name, clear_cache=True, enable_experimental_cache=True) / SimpleStru("ParamModel")

        pfem = a.get_by_name("ParamModel")
        pfem.gmsh.mesh()
        a.update_cache()

        b = Assembly(model_name, enable_experimental_cache=True)

        cache_validation(a, b)


if __name__ == "__main__":
    unittest.main()

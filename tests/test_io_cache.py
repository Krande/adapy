import time
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


class ModelCacheTests(unittest.TestCase):
    def test_simplestru_fem_cache(self):

        model_name = "ParamAssembly"

        start = time.time()
        a = Assembly(model_name, clear_cache=True, enable_experimental_cache=True) / SimpleStru("ParamModel")

        pfem = a.get_by_name("ParamModel")
        pfem.gmsh.mesh()
        time1 = time.time() - start

        a.update_cache()
        start = time.time()
        b = Assembly(model_name, enable_experimental_cache=True)
        time2 = time.time() - start
        cache_validation(a, b)

        print(f"Model generation time reduced from {time1:.2f}s to {time2:.2f}s -> {time1 / time2:.2f} x Improvement")


if __name__ == "__main__":
    unittest.main()

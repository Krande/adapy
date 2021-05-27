import unittest

from ada import Assembly
from ada._cache.reader import read_assembly_from_cache
from ada._cache.writer import write_assembly_to_cache
from ada.config import Settings
from ada.param_models.basic_module import SimpleStru

test_folder = Settings.test_dir / "cache"


class MyTestCase(unittest.TestCase):
    def test_something(self):
        a = Assembly("ParametricSite") / SimpleStru("ParametricModel")
        a.gmsh.mesh()

        cache_file = (test_folder / a.name).with_suffix(".h5")

        write_assembly_to_cache(a, cache_file)
        a2 = read_assembly_from_cache(cache_file)

        for nA, nB in zip(a.fem.nodes, a2.fem.nodes):
            assert nA == nB

        for nA, nB in zip(a.fem.elements, a2.fem.elements):
            assert nA == nB

        print(a)
        print(a2)


if __name__ == "__main__":
    unittest.main()

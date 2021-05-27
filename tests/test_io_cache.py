import unittest

from ada import Assembly
from ada.config import Settings
from ada._cache.writer import write_assembly_to_cache
from ada._cache.reader import read_assembly_from_cache
from ada.param_models.basic_module import SimpleStru

test_folder = Settings.test_dir / "cache"


class MyTestCase(unittest.TestCase):
    def test_something(self):
        a = Assembly("ParametricSite") / SimpleStru("ParametricModel")
        a.gmsh.mesh()
        cache_file = (test_folder / a.name).with_suffix(".h5")

        write_assembly_to_cache(a, cache_file)
        a2 = read_assembly_from_cache(cache_file)

if __name__ == "__main__":
    unittest.main()

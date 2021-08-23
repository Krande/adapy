import unittest

from ada import Assembly, Part, Section


class TestEqualityProtocol(unittest.TestCase):
    def test_section_equal(self):
        import copy

        secvar = dict(
            sec_type="IG",
            h=0.8,
            w_top=0.2,
            w_btn=0.2,
            t_fbtn=0.01,
            t_ftop=0.01,
            t_w=0.01,
        )
        sec1 = Section(name="sec1", **secvar)
        sec2 = copy.deepcopy(sec1)
        sec2.name = "sec2"
        sec3 = Section(name="sec3", **secvar)
        list_of_secs = [sec1, sec2, sec3]
        self.assertTrue(sec1 == sec1)
        self.assertTrue(sec1 in list_of_secs)
        self.assertFalse(Section(name="sec4", **secvar) in list_of_secs)

    def test_parts_list(self):
        a = Assembly("MyAssembly")
        # Level 1
        part = Part("my_part1")
        a.add_part(part)
        a.add_part(Part("my_part2"))
        # Level 2
        part.add_part(Part("my_part1_subpart1"))
        part.add_part(Part("my_part1_subpart2"))
        # Level 3
        subpart3 = Part("my_part1_subpart3")
        subpart3.add_part(Part("my_part1_subpart3_sub1"))
        part.add_part(subpart3)
        list_of_ps = a.get_all_parts_in_assembly()

        self.assertEqual(len(list_of_ps), 6)


if __name__ == "__main__":
    unittest.main()

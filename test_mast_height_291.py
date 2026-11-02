import unittest
from radioplanner.profile import required_antenna_raises

class MastHeightTests(unittest.TestCase):
    def test_middle_obstacle(self):
        rows=[dict(clearance_m=0,fresnel_radius_m=0),dict(clearance_m=-2,fresnel_radius_m=5),dict(clearance_m=0,fresnel_radius_m=0)]
        r=required_antenna_raises(dict(rows=rows,fresnel_fraction=.6))
        self.assertAlmostEqual(r['raise_a_only_m'],10)
        self.assertAlmostEqual(r['raise_b_only_m'],10)
        self.assertAlmostEqual(r['raise_both_each_m'],5)
    def test_clear(self):
        rows=[dict(clearance_m=0,fresnel_radius_m=0),dict(clearance_m=8,fresnel_radius_m=5),dict(clearance_m=0,fresnel_radius_m=0)]
        self.assertEqual(required_antenna_raises(dict(rows=rows,fresnel_fraction=.6))['raise_a_only_m'],0)

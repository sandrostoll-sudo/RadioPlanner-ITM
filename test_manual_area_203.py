import unittest
from radioplanner.acoustic_grid import field
from radioplanner.buildings import _tile_bounds

class ManualAreaTests(unittest.TestCase):
    def test_manual_bounds_are_used(self):
        s=[dict(latitude=49.3,longitude=7.0,pattern='omni',azimuth_deg=0,horn_count=1)]
        result=field(s,size=20,bounds=(49.29,6.99,49.31,7.01))
        self.assertAlmostEqual(result['bounds'][0][0],49.29,places=5)
        self.assertAlmostEqual(result['bounds'][1][1],7.01,places=5)
    def test_small_area_one_tile(self):
        self.assertEqual(len(list(_tile_bounds(49.29,6.99,49.31,7.01))),1)

if __name__=='__main__':unittest.main()

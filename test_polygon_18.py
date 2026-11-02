import unittest
from radioplanner.coverage_analysis import point_in_polygon,analyze

class PolygonTests(unittest.TestCase):
    def test_polygon(self):
        polygon=[[49,7],[49,7.1],[49.1,7.1],[49.1,7]]
        self.assertTrue(point_in_polygon(49.05,7.05,polygon))
        self.assertFalse(point_in_polygon(49.15,7.05,polygon))
    def test_mask(self):
        sirens=[{'latitude':49.05,'longitude':7.05,'name':'Probe','directionality':'omni'}]
        polygon=[[49.04,7.04],[49.04,7.06],[49.06,7.06],[49.06,7.04]]
        result=analyze(sirens,49.05,7.05,5,5,polygon=polygon,cells=30)
        self.assertGreater(result['sampled_cells'],0)
        self.assertLess(result['sampled_cells'],result['grid_width']*result['grid_height'])
        self.assertAlmostEqual(result['served_km2']+result['gap_km2'],result['area_km2'])
if __name__=='__main__':unittest.main()

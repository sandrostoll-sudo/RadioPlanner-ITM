import unittest
from radioplanner.profile import great_circle_points

class ProfileInteractionTests(unittest.TestCase):
    def test_endpoints_local(self):
        a=(49.3,6.7);b=(49.4,7.0)
        points=great_circle_points(a,b,301)
        self.assertEqual(len(points),301)
        for got,want in ((points[0],a),(points[-1],b)):
            self.assertAlmostEqual(got[0],want[0],places=6)
            self.assertAlmostEqual(got[1],want[1],places=6)
    def test_online_linear(self):
        self.assertEqual(great_circle_points((0,0),(2,4),3,True)[1],(1,2))

import unittest
from radioplanner.profile import analyze, TerrainError

class FresnelFractionTests(unittest.TestCase):
    def test_fraction_validation(self):
        with self.assertRaises(TerrainError):
            analyze(None,(49,7),(49.01,7),20,20,5800,fresnel_fraction=1.5)
    def test_online_fraction_validation(self):
        from radioplanner.profile import analyze_online
        with self.assertRaises(TerrainError):
            analyze_online((49,7),(49.01,7),20,20,5800,4/3,301,'cache',-0.1)

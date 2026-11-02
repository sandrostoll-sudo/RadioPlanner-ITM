import unittest
from radioplanner.propagation import scenario_losses

class TestScenario(unittest.TestCase):
    def test_losses(self):
        x=scenario_losses(10,18000,2,0.1,0.5)
        self.assertAlmostEqual(x['rain_db'],10)
        self.assertAlmostEqual(x['atmosphere_db'],1)
        self.assertAlmostEqual(x['additional_db'],11)
    def test_zero(self):
        self.assertEqual(scenario_losses(2,5800)['additional_db'],0)
    def test_bad(self):
        with self.assertRaises(ValueError):scenario_losses(10,18000,-1)
        with self.assertRaises(ValueError):scenario_losses(10,18000,0,0,1.2)

import sys
import types
import unittest
from unittest.mock import patch
from radioplanner.propagation import regional_rain_rate


class RegionalRainRateTest(unittest.TestCase):
    def test_uses_p837_rainfall_rate(self):
        calls = []
        class Result:
            value = 32.4
        def rainfall_rate(lat, lon, percentage):
            calls.append((lat, lon, percentage))
            return Result()
        fake = types.SimpleNamespace(rainfall_rate=rainfall_rate)
        with patch.dict(sys.modules, {'itur': types.ModuleType('itur'), 'itur.models': types.ModuleType('itur.models'), 'itur.models.itu837': fake}):
            sys.modules['itur.models'].itu837 = fake
            self.assertAlmostEqual(regional_rain_rate(49.3, 7.0), 32.4)
        self.assertEqual(calls, [(49.3, 7.0, 0.01)])

    def test_invalid_coordinates(self):
        with self.assertRaises(ValueError):
            regional_rain_rate(100, 7)


if __name__ == '__main__':
    unittest.main()

import unittest
from radioplanner.dmr_area import calculate_area

REPEATER = dict(latitude=49.4, longitude=7.1, frequency_mhz=440, power_w=10, gain_dbi=2, loss_db=1, rx_sensitivity_dbm=-115)
TERMINAL = (1,0,0,-115,0)

class DiagnosticsTests(unittest.TestCase):
    def test_absolute_eight_classes_and_terrain_statistics(self):
        cells, stats = calculate_area(REPEATER, TERMINAL, steps=11, mode='level', terrain_loss_fn=lambda a,b,d: 7.5)
        self.assertEqual(len(stats['band_counts']), 8)
        self.assertEqual(stats['min_terrain_loss_db'], 7.5)
        self.assertEqual(stats['max_terrain_loss_db'], 7.5)
        self.assertEqual(sum(stats['band_counts'])+stats['below_minimum'], stats['count'])
    def test_relative_has_five_classes(self):
        _, stats = calculate_area(REPEATER, TERMINAL, steps=11, mode='relative')
        self.assertEqual(len(stats['band_counts']), 5)

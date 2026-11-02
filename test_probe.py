import unittest
from radioplanner.acoustic_grid import level_at
class TestProbe(unittest.TestCase):
 def test_distance_and_level(self):
  s=[dict(name='A',latitude=49.0,longitude=7.0,pattern='omni',horn_count=1,acoustic_reference_db=100,acoustic_reference_distance_m=1)]
  r=level_at(s,49.0,7.0,attenuation=0)
  self.assertAlmostEqual(r['level_db'],100)
  self.assertAlmostEqual(r['contributions'][0]['distance_m'],1)
 def test_energy_sum(self):
  s=[dict(name='A',latitude=49.0,longitude=7.0,pattern='omni',horn_count=1,acoustic_reference_db=100,acoustic_reference_distance_m=1)]
  self.assertAlmostEqual(level_at(s*2,49.0,7.0,attenuation=0)['level_db'],103.0103,places=3)

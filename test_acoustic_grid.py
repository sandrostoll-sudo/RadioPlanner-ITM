import unittest
import base64
from radioplanner.acoustic_grid import field, direction_gain

class TestRaster(unittest.TestCase):
    def test_png(self):
        s={'latitude':49.3,'longitude':7.0,'pattern':'Unidirektional','azimuth_deg':90,'horn_count':1}
        out=field([s],size=48)
        self.assertTrue(base64.b64decode(out['image'].split(',')[1]).startswith(b'\x89PNG'))
        self.assertEqual(out['width'],48)
    def test_direction(self):
        self.assertGreater(direction_gain('Unidirektional',90,1,90),direction_gain('Unidirektional',90,1,270))
    def test_multi(self):
        s={'latitude':49.3,'longitude':7.0,'pattern':'Omnidirektional','azimuth_deg':0,'horn_count':1}
        self.assertTrue(field([s,dict(s,longitude=7.01)],size=32)['image'])

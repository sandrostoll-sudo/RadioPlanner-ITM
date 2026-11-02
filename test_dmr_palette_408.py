import unittest
from radioplanner.dmr_palette import DEFAULT_BANDS,validate_bands,band_for_dbm,load_bands

class FakeSettings:
    def value(self,key,default=None): return default

class TestPalette(unittest.TestCase):
    def test_thresholds(self):
        bands=validate_bands(DEFAULT_BANDS)
        self.assertEqual(band_for_dbm(-74,bands),0)
        self.assertEqual(band_for_dbm(-75,bands),0)
        self.assertEqual(band_for_dbm(-76,bands),1)
        self.assertEqual(band_for_dbm(-120,bands),7)
        self.assertIsNone(band_for_dbm(-121,bands))
    def test_invalid(self):
        bands=[dict(b) for b in DEFAULT_BANDS]
        bands[1]['min']=-70
        with self.assertRaises(ValueError):validate_bands(bands)
    def test_default(self):
        self.assertEqual(load_bands(FakeSettings()),validate_bands(DEFAULT_BANDS))

if __name__=='__main__':unittest.main()

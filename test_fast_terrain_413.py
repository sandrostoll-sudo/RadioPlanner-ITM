import tempfile
import unittest
from pathlib import Path

class FastTerrainTests(unittest.TestCase):
    def test_fast_sampling_matches_existing_sampling(self):
        try:
            import rasterio
            import numpy as np
            from rasterio.transform import from_origin
        except ImportError:
            self.skipTest('rasterio/numpy not installed')
        from radioplanner.terrain import TerrainSampler
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.tif'
            values = np.arange(128 * 128, dtype='float32').reshape((128, 128))
            values[50, 50] = -9999
            with rasterio.open(path, 'w', driver='GTiff', width=128, height=128, count=1,
                               dtype='float32', crs='EPSG:4326', transform=from_origin(7.0, 50.0, 0.001, 0.001), nodata=-9999) as dst:
                dst.write(values, 1)
            with TerrainSampler(folder) as sampler:
                for row, col in [(0, 0), (4, 6), (40, 50), (63, 63), (64, 64), (100, 120), (127, 127)]:
                    lat, lon = 50 - (row + .5) * .001, 7 + (col + .5) * .001
                    self.assertEqual(sampler.sample(lat, lon)['height'], sampler.sample_fast(lat, lon)['height'])
                self.assertGreater(sampler._fast_stats['block_hits'], 0)
                lat, lon = 50 - 50.5 * .001, 7 + 50.5 * .001
                with self.assertRaises(Exception):
                    sampler.sample_fast(lat, lon)

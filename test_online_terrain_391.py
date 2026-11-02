import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from radioplanner.online_terrain import elevations
from radioplanner.terrain import TerrainError

class Response:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, *args): return b'{"elevation":[120.0]}'

class TestOnlineTerrain391(unittest.TestCase):
    def test_cache_prevents_second_request(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('radioplanner.online_terrain.urlopen', return_value=Response()) as mock:
                self.assertEqual(elevations([(49, 7)], d), [120.0])
                self.assertEqual(elevations([(49, 7)], d), [120.0])
                self.assertEqual(mock.call_count, 1)

    def test_429_retry_then_success(self):
        with tempfile.TemporaryDirectory() as d:
            error=HTTPError('url', 429, 'Too Many Requests', {}, None)
            with patch('radioplanner.online_terrain.urlopen', side_effect=[error, Response()]), patch('time.sleep'):
                self.assertEqual(elevations([(49, 7)], d), [120.0])

    def test_429_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            error=HTTPError('url', 429, 'Too Many Requests', {}, None)
            with patch('radioplanner.online_terrain.urlopen', side_effect=[error]*3) as mock, patch('time.sleep'):
                with self.assertRaisesRegex(TerrainError, 'HTTP 429'):
                    elevations([(49, 7)], d)
                self.assertEqual(mock.call_count, 3)

if __name__=='__main__': unittest.main()

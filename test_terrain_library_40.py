import tempfile
import unittest
from pathlib import Path
from radioplanner.terrain_library import dataset_files, import_geotiff, download_geotiff

class TerrainLibraryTests(unittest.TestCase):
    def test_import_and_list(self):
        with tempfile.TemporaryDirectory() as root:
            src=Path(root)/'test.tif';src.write_bytes(b'fake tif')
            dest=Path(root)/'library'
            self.assertEqual(import_geotiff(src,dest),dest/'test.tif')
            self.assertEqual(dataset_files(dest),[dest/'test.tif'])
            with self.assertRaises(FileExistsError):import_geotiff(src,dest)
    def test_reject_other_files(self):
        with tempfile.TemporaryDirectory() as root:
            src=Path(root)/'file.txt';src.write_text('x')
            with self.assertRaises(ValueError):import_geotiff(src,Path(root)/'lib')
    def test_download_rejects_non_https(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):download_geotiff('http://example.com/a.tif',root)
            with self.assertRaises(ValueError):download_geotiff('https://example.com/a.zip',root)
if __name__=='__main__':unittest.main()

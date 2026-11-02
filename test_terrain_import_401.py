import tempfile
import unittest
from pathlib import Path
from radioplanner.terrain_library import import_geotiffs, dataset_files

class FolderImportTests(unittest.TestCase):
    def test_recursive_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'source'; dest=root/'dest'
            (source/'a').mkdir(parents=True);(source/'b').mkdir()
            (source/'a'/'tile.tif').write_bytes(b'abc')
            (source/'b'/'tile.tif').write_bytes(b'def')
            paths=dataset_files(source)
            result=import_geotiffs(paths,dest,base_folder=source)
            self.assertEqual(result['imported'],2)
            self.assertEqual((dest/'a'/'tile.tif').read_bytes(),b'abc')
            self.assertEqual((dest/'b'/'tile.tif').read_bytes(),b'def')
            again=import_geotiffs(paths,dest,base_folder=source)
            self.assertEqual(again['skipped'],2)
    def test_cancellation(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'tile.tif';source.write_bytes(b'abc')
            with self.assertRaises(InterruptedError):
                import_geotiffs([source],Path(temp)/'dest',cancelled=lambda:True)

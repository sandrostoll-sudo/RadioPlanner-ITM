import tempfile
import unittest
from pathlib import Path
from radioplanner.database import Database

class ScenarioPersistenceTest(unittest.TestCase):
    def test_columns_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db')
            columns={r['name'] for r in db.conn.execute('PRAGMA table_info(radio_links)')}
            self.assertTrue({'rain_specific_db_km','gas_specific_db_km','wet_path_fraction'} <= columns)

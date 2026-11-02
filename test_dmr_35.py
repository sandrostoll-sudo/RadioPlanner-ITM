import tempfile
import unittest
from pathlib import Path
from radioplanner.database import Database
from radioplanner.dmr import evaluate_link

class Dmr35Tests(unittest.TestCase):
    def test_device_parameters_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db')
            pid=db.conn.execute("INSERT INTO projects(name) VALUES ('DMR')").lastrowid
            rid=db.save_dmr_device(pid,'Station','fixed',49.3,7.0,power_w=40,gain_dbi=7,loss_db=3,height_m=15,rx_sensitivity_dbm=-120)
            row=db.dmr_devices(pid)[0]
            self.assertEqual(row['id'],rid)
            self.assertEqual((row['power_w'],row['gain_dbi'],row['loss_db'],row['height_m'],row['rx_sensitivity_dbm']),(40,7,3,15,-120))
            db.close()
    def test_independent_receiver_sensitivity(self):
        a=evaluate_link(5,440,25,5,2,5,-3,1,-117,10)
        b=evaluate_link(5,440,25,5,2,5,-3,1,-117,10,repeater_sensitivity_dbm=-125)
        self.assertAlmostEqual(a['downlink_margin_db'],b['downlink_margin_db'])
        self.assertAlmostEqual(b['uplink_margin_db']-a['uplink_margin_db'],8)
if __name__=='__main__': unittest.main()

import tempfile
import unittest
from pathlib import Path
from radioplanner.database import Database
from radioplanner.acoustic_grid import field

class CatalogTests(unittest.TestCase):
    def test_catalog_and_site_assignment(self):
        with tempfile.TemporaryDirectory() as td:
            db=Database(Path(td)/'db.sqlite3')
            project=db.create_project('Test')
            values=dict(manufacturer='Eigener Hersteller',model='S-2400',power_w=2400,reference_db=110,reference_distance_m=30,pattern='Unidirektional',horn_count=2,notes='Testwert')
            model_id=db.save_siren_model(values)
            self.assertEqual(db.siren_model(model_id)['reference_distance_m'],30)
            site=dict(name='Sirene',latitude=49,longitude=7,manufacturer='Eigener Hersteller',model='S-2400',siren_type='Elektronisch',power_w=2400,pattern='Unidirektional',azimuth_deg=90,mounting_height_m=12,horn_count=2,notes='',siren_model_id=model_id)
            db.save_siren(project,site)
            self.assertEqual(db.sirens(project)[0]['siren_model_id'],model_id)
            with self.assertRaises(ValueError):db.delete_siren_model(model_id)
            db.close()
    def test_individual_acoustic_levels(self):
        a=dict(latitude=49,longitude=7,pattern='Omnidirektional',azimuth_deg=0,horn_count=1,acoustic_reference_db=110,acoustic_reference_distance_m=30)
        b=dict(a,acoustic_reference_db=120)
        x=field([a],threshold=80,size=35)
        y=field([b],threshold=80,size=35)
        self.assertNotEqual(x['image'],y['image'])

if __name__=='__main__':unittest.main()

import unittest
from pathlib import Path

class Regression151(unittest.TestCase):
    def test_catalog_selection_is_signal_blocked_for_existing_site(self):
        source=(Path(__file__).parent/'radioplanner'/'main.py').read_text()
        self.assertIn('catalog.blockSignals(True)\n                catalog.setCurrentIndex(idx)\n                catalog.blockSignals(False)',source)
        self.assertIn('catalog.currentIndexChanged.connect(apply_catalog)',source)
    def test_whitespace_is_stripped_for_angles(self):
        source=(Path(__file__).parent/'radioplanner'/'main.py').read_text()
        self.assertIn("[float(x.strip()) for x in horn_angles.text().replace(';',',').split(',')]",source)
        self.assertEqual([float(x.strip()) for x in '0, 90, 180, 270'.split(',')],[0,90,180,270])

if __name__=='__main__': unittest.main()

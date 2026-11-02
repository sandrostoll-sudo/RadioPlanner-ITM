import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from radioplanner.itm_adapter import ITMSettings
from radioplanner.itm_native import NativeITM, NativeITMError


class TestNativeBridge(unittest.TestCase):
    def test_missing_dll_fails_closed(self):
        with self.assertRaises(NativeITMError):
            NativeITM('/does/not/exist/itm.dll')

    def test_abi_with_test_stub_not_real_itm(self):
        # This synthetic stub ONLY tests ABI wiring and errors, NOT ITM physics.
        source = r'''#include "itm_bridge.h"
int rp_itm_point_to_point(const double *h, int n, double s, double tx,
 double rx, double f, double c, double p, double r, double t,
 int climate, int pol, double loc, double conf, double unused, double *loss) {
 if(n < 3 || s <= 0 || f <= 0) return 7;
 *loss = h[0] + h[n-1] + s/1000.0 + f/100.0 + tx + rx;
 return 0;
}'''
        project_root = Path(__file__).resolve().parent
        header_dir = project_root / 'native_itm'
        self.assertTrue((header_dir / 'itm_bridge.h').is_file(),
                        f'Missing native bridge header: {header_dir / "itm_bridge.h"}')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub_c = root / 'stub.c'
            stub_c.write_text(source, encoding='utf-8')
            library = root / ('stub.dll' if os.name == 'nt' else 'stub.so')
            command = ['gcc', '-shared']
            if os.name != 'nt':
                command.append('-fPIC')
            command.extend(['-I', str(header_dir), str(stub_c), '-o', str(library)])
            subprocess.run(command, check=True)

            engine = NativeITM(library)
            result = engine(heights_m=[100, 110, 120], spacing_m=90,
                            tx_height_m=12, rx_height_m=1.5,
                            frequency_mhz=80, settings=ITMSettings())
            self.assertAlmostEqual(result, 234.39)
            with self.assertRaises(ValueError):
                engine(heights_m=[100, float('nan'), 120], spacing_m=90,
                       tx_height_m=12, rx_height_m=1.5,
                       frequency_mhz=80, settings=ITMSettings())


if __name__ == '__main__':
    unittest.main()

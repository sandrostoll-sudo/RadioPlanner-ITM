import ctypes
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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'stub.c').write_text(source)
            subprocess.run(['gcc','-shared','-fPIC','-I',str(Path(__file__).parent),
                 str(root/'stub.c'),'-o',str(root/'stub.so')],check=True,capture_output=True)
            engine = NativeITM(root/'stub.so')
            result = engine(heights_m=[100,110,120],spacing_m=90,
                tx_height_m=12,rx_height_m=1.5,frequency_mhz=80,settings=ITMSettings())
            self.assertAlmostEqual(result,234.39)
            with self.assertRaises(ValueError):
                engine(heights_m=[100,float('nan'),120],spacing_m=90,
                    tx_height_m=12,rx_height_m=1.5,frequency_mhz=80,settings=ITMSettings())

if __name__ == '__main__': unittest.main()

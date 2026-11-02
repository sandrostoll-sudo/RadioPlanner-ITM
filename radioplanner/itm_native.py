"""Native ITM bridge. Requires an external, independently verified DLL.

The DLL must export rp_itm_point_to_point, as defined in itm_bridge.h.
No engine is bundled; this adapter never substitutes another propagation model.
"""
import ctypes
import math
from pathlib import Path

class NativeITMError(RuntimeError):
    pass

class NativeITM:
    def __init__(self, library_path):
        path = Path(library_path).expanduser().resolve()
        if not path.is_file():
            raise NativeITMError(f"ITM-Bibliothek nicht gefunden: {path}")
        try:
            self._dll = ctypes.CDLL(str(path))
            self._predict = self._dll.rp_itm_point_to_point
        except (OSError, AttributeError) as exc:
            raise NativeITMError("DLL nicht ladbar oder rp_itm_point_to_point fehlt") from exc
        self._predict.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_int,
            ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_int, ctypes.c_int, ctypes.c_double, ctypes.c_double,
            ctypes.c_double, ctypes.POINTER(ctypes.c_double)]
        self._predict.restype = ctypes.c_int

    def __call__(self, *, heights_m, spacing_m, tx_height_m, rx_height_m,
                 frequency_mhz, settings):
        settings.validate()
        heights = [float(v) for v in heights_m]
        if len(heights) < 3 or any(not math.isfinite(h) for h in heights):
            raise ValueError("ITM: ungueltiges Hoehenprofil")
        if not all(math.isfinite(float(v)) and float(v) > 0 for v in
                   (spacing_m, tx_height_m, rx_height_m, frequency_mhz)):
            raise ValueError("ITM: ungueltige Streckenparameter")
        data = (ctypes.c_double * len(heights))(*heights)
        loss = ctypes.c_double(float('nan'))
        status = self._predict(data, len(heights), float(spacing_m),
            float(tx_height_m), float(rx_height_m), float(frequency_mhz),
            settings.conductivity_s_m, settings.relative_permittivity,
            settings.refractivity_n, settings.time_percent,
            settings.climate, settings.polarization, settings.location_percent,
            settings.confidence_percent, 0.0, ctypes.byref(loss))
        if status != 0:
            raise NativeITMError(f"ITM-Engine meldet Fehlercode {status}")
        if not math.isfinite(loss.value) or loss.value < 0:
            raise NativeITMError("ITM-Engine lieferte keinen gueltigen Gesamtpfadverlust")
        return loss.value

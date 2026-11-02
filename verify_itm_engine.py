"""Manual smoke test of a REAL RadioPlanner ITM bridge library.

Usage: python verify_itm_engine.py PATH_TO_BRIDGE_DLL_OR_SO
This does not certify ITM accuracy; compare output with independently
calculated NTIA reference cases before enabling coverage mapping.
"""
import argparse
import json
import math
import sys
from radioplanner.itm_adapter import ITMSettings
from radioplanner.itm_native import NativeITM, NativeITMError


def run_check(path):
    engine = NativeITM(path)
    settings = ITMSettings()
    cases = [
        ('flat_1km', [100.0] * 12, 1000.0 / 11),
        ('flat_10km', [100.0] * 102, 10000.0 / 101),
        ('ridge_10km', [100.0] * 45 + [250.0] * 12 + [100.0] * 45, 10000.0 / 101),
    ]
    results = []
    for name, heights, spacing in cases:
        loss = engine(heights_m=heights, spacing_m=spacing,
                      tx_height_m=12.0, rx_height_m=1.5,
                      frequency_mhz=80.0, settings=settings)
        if not math.isfinite(loss) or loss < 0:
            raise NativeITMError(f'{name}: invalid loss {loss}')
        results.append({'case': name, 'loss_db': round(loss, 4),
                        'distance_m': round(spacing * (len(heights)-1), 2)})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library', help='Path to independently built rp_itm_point_to_point DLL')
    args = parser.parse_args()
    try:
        print(json.dumps({'engine': args.library, 'cases': run_check(args.library),
                          'warning': 'Smoke test only; not proof of NTIA ITM correctness.'}, indent=2))
    except (NativeITMError, ValueError, OSError) as exc:
        print(f'ITM verification failed: {exc}', file=sys.stderr)
        sys.exit(1)

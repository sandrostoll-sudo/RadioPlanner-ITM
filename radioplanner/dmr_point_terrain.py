"""Point-to-point terrain diffraction screening with a dominant knife edge.

Not a validated propagation model; excludes clutter and multipath. Missing
terrain samples raise an error rather than silently treating them as flat.
"""
import math
from .dmr import distance_km


def diffraction_loss(heights, distance_m, tx_height, rx_height, frequency_mhz):
    if len(heights) < 3 or distance_m <= 0:
        return 0.0
    wavelength = 299.792458 / frequency_mhz
    start = heights[0] + tx_height
    end = heights[-1] + rx_height
    max_v = -100.0
    for j in range(1, len(heights)-1):
        t = j / (len(heights)-1)
        d1, d2 = distance_m*t, distance_m*(1-t)
        bulge = d1*d2 / (2*(4/3)*6371000)
        obstruction = heights[j] + bulge - (start + (end-start)*t)
        v = obstruction * math.sqrt(2*(d1+d2)/(wavelength*d1*d2))
        max_v = max(max_v, v)
    if max_v <= -0.78:
        return 0.0
    return max(0.0, 6.9 + 20*math.log10(math.sqrt((max_v-0.1)**2+1)+max_v-0.1))


def point_model(repeater, folder, rx_height=1.5, spacing_m=180, progress=None):
    """Return (loss_fn, close_fn); caller must close even on error."""
    from .terrain import TerrainSampler
    sampler = TerrainSampler(folder)
    lat0, lon0 = float(repeater['latitude']), float(repeater['longitude'])
    freq, tx_height = float(repeater['frequency_mhz']), float(repeater['height_m'])
    cache = {}
    def height(lat, lon):
        # Quantization avoids duplicate raster reads on neighboring profiles.
        key = (round(lat, 6), round(lon, 6))
        if key not in cache:
            cache[key] = sampler.sample_fast(lat, lon)['height']
        return cache[key]
    def loss_fn(lat, lon, km):
        n = max(2, int(math.ceil(km*1000/spacing_m)))
        heights = [height(lat0+(lat-lat0)*i/n, lon0+(lon-lon0)*i/n) for i in range(n+1)]
        return diffraction_loss(heights, km*1000, tx_height, rx_height, freq)
    return loss_fn, sampler.close

"""Reproducible terrain-profile export for external ITM validation.

This is an interchange format, NOT a propagation model.
"""
import csv
import json
import math
from pathlib import Path

from .dmr import distance_km
from .itm_adapter import ITMSettings, _great_circle_points


def export_itm_profile(repeater, receiver_lat, receiver_lon, terrain_folder,
                       destination, frequency_mhz, rx_height_m=1.5,
                       spacing_m=90.0, settings=None, sampler=None):
    """Export evenly spaced terrain elevations and calculation metadata.

    A caller-supplied sampler supports deterministic tests. Otherwise TerrainSampler
    is opened and closed here. Raises if any elevation is unavailable.
    """
    settings = settings or ITMSettings()
    settings.validate()
    if not (20 <= frequency_mhz <= 20000):
        raise ValueError('ITM-Frequenz ausserhalb des Modellbereichs 20-20000 MHz')
    if not (0 < spacing_m <= 1000 and 0 < rx_height_m < 1000):
        raise ValueError('Ungueltige Profilparameter')
    lat0, lon0 = float(repeater['latitude']), float(repeater['longitude'])
    tx_height_m = float(repeater['height_m'])
    if not 0 < tx_height_m < 1000:
        raise ValueError('Ungueltige Senderantennenhoehe')
    length_m = distance_km(lat0, lon0, receiver_lat, receiver_lon) * 1000
    if not math.isfinite(length_m) or length_m <= 0:
        raise ValueError('Sender und Empfaenger muessen verschiedene Standorte haben')
    count = max(2, math.ceil(length_m / spacing_m))
    step = length_m / count
    own_sampler = sampler is None
    if own_sampler:
        from .terrain import TerrainSampler
        sampler = TerrainSampler(terrain_folder)
    try:
        points = _great_circle_points(lat0, lon0, receiver_lat, receiver_lon, count)
        heights = []
        for lat, lon in points:
            value = sampler.sample_fast(lat, lon)
            height = float(value['height'])
            if not math.isfinite(height):
                raise ValueError('Gelaendeprofil enthaelt ungueltige Hoehen')
            heights.append(height)
    finally:
        if own_sampler:
            sampler.close()
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        'format': 'radioplanner-itm-profile-v1',
        'note': 'Terrain input only; no Longley-Rice result has been calculated.',
        'transmitter': {'latitude': lat0, 'longitude': lon0, 'antenna_agl_m': tx_height_m},
        'receiver': {'latitude': receiver_lat, 'longitude': receiver_lon, 'antenna_agl_m': rx_height_m},
        'frequency_mhz': frequency_mhz,
        'distance_m': length_m,
        'sample_spacing_m': step,
        'segments': count,
        'itm_pfl': [count, step, *heights],
        'settings': vars(settings),
    }
    target.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    csv_path = target.with_suffix('.csv')
    with csv_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['index', 'distance_m', 'latitude', 'longitude', 'terrain_height_m'])
        for i, ((lat, lon), height) in enumerate(zip(points, heights)):
            writer.writerow([i, round(i * step, 4), lat, lon, height])
    return target, csv_path

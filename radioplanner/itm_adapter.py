"""Strict integration boundary for a future validated Longley-Rice/ITM engine.

This module DOES NOT implement ITM. It samples terrain profiles and delegates
prediction to an explicitly supplied, independently verified engine callable.
No silent FSPL fallback is permitted.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ITMSettings:
    conductivity_s_m: float = 0.008
    relative_permittivity: float = 13.0
    refractivity_n: float = 301.0
    climate: int = 5  # Continental temperate (ITM climate code)
    polarization: int = 1  # Vertical (ITM polarization code)
    time_percent: float = 50.0
    location_percent: float = 50.0
    confidence_percent: float = 70.0

    def validate(self):
        if not (0 < self.conductivity_s_m <= 10):
            raise ValueError('ITM: ungueltige Bodenleitfaehigkeit')
        if not (1 <= self.relative_permittivity <= 100):
            raise ValueError('ITM: ungueltige Dielektrizitaetskonstante')
        if not (200 <= self.refractivity_n <= 450):
            raise ValueError('ITM: ungueltiger Brechungsindex')
        if self.climate not in range(1, 8) or self.polarization not in (0, 1):
            raise ValueError('ITM: ungueltiges Klima oder Polarisation')
        if not all(0 < p < 100 for p in (self.time_percent, self.location_percent, self.confidence_percent)):
            raise ValueError('ITM: Prozentwerte muessen zwischen 0 und 100 liegen')


def _great_circle_points(lat0, lon0, lat1, lon1, segments):
    """Equally spaced points on the shortest spherical arc (not degree-linear)."""
    def xyz(lat, lon):
        a, b = math.radians(lat), math.radians(lon)
        return (math.cos(a)*math.cos(b), math.cos(a)*math.sin(b), math.sin(a))
    u, v = xyz(lat0, lon0), xyz(lat1, lon1)
    dot = max(-1.0, min(1.0, sum(x*y for x, y in zip(u, v))))
    angle = math.acos(dot)
    if angle < 1e-10:
        return [(lat0+(lat1-lat0)*i/segments, lon0+(lon1-lon0)*i/segments) for i in range(segments+1)]
    sin_angle = math.sin(angle)
    if abs(sin_angle) < 1e-10:
        raise ValueError("Antipodische Funkstrecke nicht unterstuetzt")
    result = []
    for i in range(segments+1):
        t = i/segments
        a, b = math.sin((1-t)*angle)/sin_angle, math.sin(t*angle)/sin_angle
        x, y, z = (a*u[k]+b*v[k] for k in range(3))
        result.append((math.degrees(math.atan2(z, math.hypot(x,y))), math.degrees(math.atan2(y,x))))
    return result


def create_itm_path_loss(repeater, terrain_folder, engine, rx_height_m=1.5,
                         spacing_m=90.0, settings=None):
    """Return (loss_fn, close_fn) for calculate_area(path_loss_fn=...).

    engine must accept keyword arguments: heights_m, spacing_m,
    tx_height_m, rx_height_m, frequency_mhz, settings; return total path loss dB.
    It is intentionally NOT auto-discovered: provenance and validation matter.
    """
    if not callable(engine):
        raise RuntimeError('Keine gepruefte Longley-Rice/ITM-Engine angeschlossen.')
    settings = settings or ITMSettings()
    settings.validate()
    if not (0 < rx_height_m < 1000 and 0 < spacing_m <= 1000):
        raise ValueError('Ungueltige Profilparameter')
    from .terrain import TerrainSampler
    sampler = TerrainSampler(terrain_folder)
    lat0, lon0 = float(repeater['latitude']), float(repeater['longitude'])
    tx_height_m = float(repeater['height_m'])
    if not (0 < tx_height_m < 1000):
        sampler.close()
        raise ValueError('Ungueltige Senderantennenhoehe')
    cache = {}

    def sample(lat, lon):
        key = (round(lat, 7), round(lon, 7))
        if key not in cache:
            z = float(sampler.sample_fast(lat, lon)['height'])
            if not math.isfinite(z):
                raise ValueError('Fehlende oder ungueltige Gelaendehoehe')
            cache[key] = z
        return cache[key]

    def path_loss_fn(lat, lon, distance_km, frequency_mhz):
        if not (0 < frequency_mhz and 0 < distance_km):
            raise ValueError('ITM benoetigt positive Frequenz und Distanz')
        from .dmr import distance_km as geographic_distance_km
        true_distance_km = geographic_distance_km(lat0, lon0, lat, lon)
        if true_distance_km <= 0:
            raise ValueError("ITM: Sender und Empfaenger am gleichen Ort")
        segments = max(2, int(math.ceil(true_distance_km * 1000 / spacing_m)))
        actual_spacing = true_distance_km * 1000 / segments
        heights = [sample(a, b) for a, b in _great_circle_points(lat0, lon0, lat, lon, segments)]
        loss = float(engine(heights_m=heights, spacing_m=actual_spacing,
                            tx_height_m=tx_height_m, rx_height_m=rx_height_m,
                            frequency_mhz=frequency_mhz, settings=settings))
        if not math.isfinite(loss) or loss < 0:
            raise ValueError('ITM-Engine lieferte ungueltigen Gesamtpfadverlust')
        return loss

    return path_loss_fn, sampler.close

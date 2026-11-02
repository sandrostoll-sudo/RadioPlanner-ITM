"""Schematic free-field point-source attenuation; not a validated siren prediction."""
import math

def pressure_at_range(reference_db, distance_m, extra_db_per_m=0.0):
    if distance_m < 1 or extra_db_per_m < 0:
        raise ValueError("Entfernung >= 1 m und nichtnegative Dämpfung erforderlich")
    return reference_db - 20 * math.log10(distance_m) - extra_db_per_m * (distance_m - 1)

def range_for_level(reference_db, threshold_db, extra_db_per_m=0.0):
    """Find the schematic threshold radius, capped at 30 km for display."""
    if extra_db_per_m < 0:
        raise ValueError("Dämpfung darf nicht negativ sein")
    if reference_db <= threshold_db:
        return 0.0
    lo, hi = 1.0, 30000.0
    if pressure_at_range(reference_db, hi, extra_db_per_m) >= threshold_db:
        return hi
    for _ in range(50):
        mid = (lo + hi) / 2
        if pressure_at_range(reference_db, mid, extra_db_per_m) > threshold_db: lo = mid
        else: hi = mid
    return (lo + hi) / 2

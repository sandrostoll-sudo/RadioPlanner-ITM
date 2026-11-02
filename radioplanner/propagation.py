"""Transparent scenario-based additional attenuation; NOT ITU-R availability prediction."""
import math


def scenario_losses(distance_km, frequency_mhz, rain_specific_db_per_km=0.0,
                    gas_specific_db_per_km=0.0, wet_path_fraction=1.0):
    """Compute explicit scenario losses from USER-SUPPLIED specific attenuation.

    No climate, polarization, rainfall statistics or ITU-R coefficients are inferred.
    This is deliberately not a rain-fade exceedance or annual availability model.
    """
    vals = (distance_km, frequency_mhz, rain_specific_db_per_km,
            gas_specific_db_per_km, wet_path_fraction)
    if not all(math.isfinite(float(v)) for v in vals):
        raise ValueError('Nur endliche Zahlen zulässig.')
    if distance_km <= 0 or frequency_mhz <= 0 or min(rain_specific_db_per_km, gas_specific_db_per_km, wet_path_fraction) < 0 or wet_path_fraction > 1:
        raise ValueError('Ungültige Strecken- oder Dämpfungsparameter.')
    rain = distance_km * wet_path_fraction * rain_specific_db_per_km
    gas = distance_km * gas_specific_db_per_km
    return dict(rain_db=rain, atmosphere_db=gas, additional_db=rain+gas)



def itu_specific_losses(frequency_mhz, rain_rate_mm_h, polarization='Horizontal',
                        temperature_c=15.0, pressure_hpa=1013.25,
                        water_vapor_g_m3=7.5):
    """ITU-R P.838 rain and P.676 gaseous specific losses via optional ITU-Rpy.

    The rain rate is an explicit *scenario* input, not a climatological R0.01.
    This method does not estimate annual outage probability.
    """
    f = float(frequency_mhz) / 1000.0
    r = float(rain_rate_mm_h)
    temp = float(temperature_c)
    pressure = float(pressure_hpa)
    vapor = float(water_vapor_g_m3)
    if not all(math.isfinite(x) for x in (f, r, temp, pressure, vapor)):
        raise ValueError('Alle Eingaben müssen endlich sein.')
    if not (1 <= f <= 1000 and 0 <= r <= 500 and -50 <= temp <= 60 and
            300 <= pressure <= 1100 and 0 <= vapor <= 60):
        raise ValueError('Eingaben außerhalb des unterstützten Bereichs.')
    if polarization not in ('Horizontal', 'Vertikal'):
        raise ValueError('Für ITU-R P.838 bitte horizontal oder vertikal wählen.')
    try:
        from itur.models import itu838, itu676
    except ImportError as exc:
        raise RuntimeError('Das optionale Paket itur fehlt. Installieren: py -3.12 -m pip install itur') from exc
    tau = 0 if polarization == 'Horizontal' else 90
    rain = float(itu838.rain_specific_attenuation(r, f, 0, tau).value)
    # P.676 terrestrial path model returns dB for the provided path length.
    gas = float(itu676.gaseous_attenuation_terrestrial_path(
        1.0, f, 0.0, vapor, pressure, temp + 273.15, mode='approx').value)
    if not (math.isfinite(rain) and math.isfinite(gas) and rain >= 0 and gas >= 0):
        raise ValueError('ITU-R-Modell lieferte ungültige Dämpfungswerte.')
    return dict(rain_specific_db_km=rain, gas_specific_db_km=gas,
                model='ITU-R P.838 / P.676 via ITU-Rpy',
                limitation='Szenario mit vorgegebener Regenrate, keine Jahresverfügbarkeit')


def regional_rain_rate(latitude_deg, longitude_deg):
    """ITU-R P.837 R0.01 (mm/h) at a geographic point via ITU-Rpy.

    This is a rain-rate statistic, not a P.530 rain-fade exceedance estimate.
    """
    lat, lon = float(latitude_deg), float(longitude_deg)
    if not all(math.isfinite(v) for v in (lat, lon)) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Ungültige Koordinaten für Regenstatistik.')
    try:
        from itur.models import itu837
    except ImportError as exc:
        raise RuntimeError('Das Paket itur fehlt: py -3.12 -m pip install itur') from exc
    rate = float(itu837.rainfall_rate(lat, lon, 0.01).value)
    if not math.isfinite(rate) or not 0 <= rate <= 500:
        raise ValueError('P.837 lieferte eine ungültige Regenrate.')
    return rate


def rain_availability_estimate(lat, lon, distance_km, frequency_mhz, polarization,
                               fade_margin_db, gas_loss_db=0.0):
    """Rain-only annual exceedance using ITU-R P.530 via ITU-Rpy.

    Other outage causes and rain/fading correlations are not modeled.
    The result must not be presented as total link availability.
    """
    import math
    lat, lon, d, f = map(float, (lat, lon, distance_km, frequency_mhz))
    margin, gas = float(fade_margin_db), float(gas_loss_db)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and d > 0 and f > 0):
        raise ValueError('Ungültige Streckenkoordinaten, Entfernung oder Frequenz.')
    if not all(math.isfinite(v) for v in (lat, lon, d, f, margin, gas)):
        raise ValueError('Nicht-endliche Eingabewerte.')
    if polarization not in ('Horizontal', 'Vertikal'):
        raise ValueError('Polarisation muss Horizontal oder Vertikal sein.')
    try:
        from itur.models import itu530, itu837
    except ImportError as exc:
        raise RuntimeError('Das Paket itur fehlt: py -3.12 -m pip install itur') from exc
    r001 = float(itu837.rainfall_rate(lat, lon, 0.01).value)
    tau = 0 if polarization == 'Horizontal' else 90
    threshold = margin - gas
    def attenuation(p):
        value = float(itu530.rain_attenuation(lat, lon, d, f / 1000, 0, p, tau=tau, R001=r001).value)
        if not math.isfinite(value) or value < 0:
            raise ValueError('ITU-R P.530 lieferte ungültige Regendämpfung.')
        return value
    # p is percentage of an average year for which attenuation is exceeded.
    p_min, p_max = 0.001, 5.0
    a_min, a_max = attenuation(p_min), attenuation(p_max)
    if threshold <= 0:
        return dict(status='insufficient', note='Keine positive Reserve nach Gasdämpfung; keine belastbare Regenverfügbarkeit.', r001_mm_h=r001)
    if threshold >= a_min:
        return dict(status='upper_bound', p_percent=p_min, availability_percent=100-p_min,
                    outage_hours_year=87.66*p_min, r001_mm_h=r001,
                    note='Regenbedingte Überschreitung unterhalb des untersuchten Bereichs (<0,001 %).')
    if threshold <= a_max:
        return dict(status='lower_bound', p_percent=p_max, availability_percent=100-p_max,
                    outage_hours_year=87.66*p_max, r001_mm_h=r001,
                    note='Regenbedingte Überschreitung oberhalb des untersuchten Bereichs (>5 %).')
    lo, hi = p_min, p_max
    for _ in range(45):
        mid = math.sqrt(lo * hi)
        if attenuation(mid) > threshold:
            lo = mid
        else:
            hi = mid
    p = math.sqrt(lo * hi)
    return dict(status='estimate', p_percent=p, availability_percent=100-p,
                outage_hours_year=87.66*p, r001_mm_h=r001,
                note='Nur regenbedingte Ausfälle nach P.530; keine Gesamtverfügbarkeit.')

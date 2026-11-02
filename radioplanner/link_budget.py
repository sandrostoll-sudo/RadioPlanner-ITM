"""Simplified line-of-sight microwave link budget, no availability prediction."""
import math


def calculate(distance_km, frequency_mhz, tx_power_dbm, gain_a_dbi, gain_b_dbi,
              loss_a_db, loss_b_db, rx_sensitivity_dbm, reserve_target_db=20):
    values = (distance_km, frequency_mhz, tx_power_dbm, gain_a_dbi,
              gain_b_dbi, loss_a_db, loss_b_db, rx_sensitivity_dbm, reserve_target_db)
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError('Alle Werte müssen endlich sein.')
    if distance_km <= 0 or frequency_mhz <= 0:
        raise ValueError('Entfernung und Frequenz müssen größer als null sein.')
    if loss_a_db < 0 or loss_b_db < 0 or reserve_target_db < 0:
        raise ValueError('Verluste und Zielreserve dürfen nicht negativ sein.')
    fspl = 32.44 + 20 * math.log10(distance_km) + 20 * math.log10(frequency_mhz)
    eirp = tx_power_dbm + gain_a_dbi - loss_a_db
    received = eirp - fspl + gain_b_dbi - loss_b_db
    margin = received - rx_sensitivity_dbm
    assessment = ('Rechnerisch nicht ausreichend' if margin < 0 else
                  'Geringe Reserve' if margin < reserve_target_db else
                  'Zielreserve erreicht')
    return dict(distance_km=distance_km, frequency_mhz=frequency_mhz,
                fspl_db=fspl, eirp_dbm=eirp, received_dbm=received,
                fade_margin_db=margin, reserve_target_db=reserve_target_db,
                assessment=assessment)


def status_for_margin(margin_db, target_db=20):
    """Return traffic-light status; target is a planning preference, not a reliability guarantee."""
    if not math.isfinite(float(margin_db)) or not math.isfinite(float(target_db)) or target_db < 0:
        raise ValueError('Ungültige Fadingreserve oder Zielreserve.')
    if margin_db < 0:
        return 'red'
    if margin_db < target_db:
        return 'yellow'
    return 'green'

"""Eight configurable absolute downlink dBm display bands."""
import json
import re

DEFAULT_BANDS = [
    {"min": -75, "color": "#145A32", "label": "Sehr stark"},
    {"min": -85, "color": "#28A745", "label": "Stark"},
    {"min": -95, "color": "#80C95A", "label": "Gut"},
    {"min": -100, "color": "#D7DE45", "label": "Befriedigend"},
    {"min": -105, "color": "#FFD447", "label": "Mäßig"},
    {"min": -110, "color": "#F5A142", "label": "Schwach"},
    {"min": -115, "color": "#E75B36", "label": "Sehr schwach"},
    {"min": -120, "color": "#D62839", "label": "Grenzwertig"},
]

def validate_bands(bands):
    if not isinstance(bands, list) or len(bands) != 8:
        raise ValueError('Es sind genau acht Pegelstufen erforderlich.')
    result=[]
    for b in bands:
        if not isinstance(b, dict) or isinstance(b.get('min'), bool):
            raise ValueError('Ungültige Pegelstufe.')
        value=float(b['min'])
        if not -200 <= value <= 100:
            raise ValueError('Pegelgrenze außerhalb des zulässigen Bereichs.')
        color=str(b['color']).upper()
        if not re.fullmatch(r'#[0-9A-F]{6}',color):
            raise ValueError('Ungültige Farbe.')
        label=str(b['label']).strip()
        if not label or len(label)>60:
            raise ValueError('Ungültige Bezeichnung.')
        result.append({'min':value,'color':color,'label':label})
    if any(result[i]['min'] <= result[i+1]['min'] for i in range(7)):
        raise ValueError('Pegelgrenzen müssen von oben nach unten streng absteigen.')
    return result

def load_bands(settings):
    try:
        return validate_bands(json.loads(settings.value('dmr/dbm_bands', '')))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return validate_bands(DEFAULT_BANDS)

def band_for_dbm(level, bands):
    for i, band in enumerate(bands):
        if level >= band['min']:
            return i
    return None  # Under lowest threshold: transparent.

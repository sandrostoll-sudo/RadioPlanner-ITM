"""Versioned derived results with engineering-input validation."""
import json
from pathlib import Path

# Exclude display-only and previously computed terrain assessment columns.
FIELDS = ('station_a_id','station_b_id','frequency_ghz','height_a_m','height_b_m',
          'tx_power_dbm','gain_a_dbi','gain_b_dbi','loss_a_db','loss_b_db',
          'rx_sensitivity_dbm','reserve_target_db','itu_rain_rate_mm_h',
          'itu_polarization','itu_temperature_c','itu_pressure_hpa','itu_vapor_g_m3')

def signature(link, stations):
    a=stations.get(link['station_a_id']); b=stations.get(link['station_b_id'])
    if not a or not b: return None
    return [a['latitude'],a['longitude'],b['latitude'],b['longitude']] + [link[k] if k in link.keys() else None for k in FIELDS]

def _path(folder, project_id, link_id):
    return Path(folder)/f'analysis_{project_id}_{link_id}.json'

def save_analysis(folder, project_id, link, stations, result):
    sig=signature(link,stations)
    if sig is None: raise ValueError('Standorte fehlen')
    path=_path(folder,project_id,link['id']);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'version':1,'signature':sig,'result':result},allow_nan=False),encoding='utf-8')

def read_analysis(folder, project_id, link, stations):
    try:
        obj=json.loads(_path(folder,project_id,link['id']).read_text(encoding='utf-8'))
        if obj.get('version')!=1 or obj.get('signature')!=signature(link,stations):return None
        result=obj['result']
        return result if isinstance(result,dict) else None
    except (OSError,ValueError,TypeError,KeyError):return None

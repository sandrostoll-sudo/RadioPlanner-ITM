"""Store profile samples only for exactly matching link engineering inputs."""
import json
from pathlib import Path

def signature(link, a, b):
    return [a['latitude'],a['longitude'],b['latitude'],b['longitude'],link['frequency_ghz'],link['height_a_m'],link['height_b_m']]

def _file(folder, project_id, link_id):
    return Path(folder)/f"project_{project_id}_link_{link_id}.json"

def save_profile(folder, project_id, link, a, b, result):
    path=_file(folder,project_id,link['id'])
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'signature':signature(link,a,b),'profile':result},allow_nan=False),encoding='utf-8')

def read_profile(folder, project_id, link, stations):
    a=stations.get(link['station_a_id']);b=stations.get(link['station_b_id'])
    if not a or not b:return None
    try:
        obj=json.loads(_file(folder,project_id,link['id']).read_text(encoding='utf-8'))
        if obj['signature']!=signature(link,a,b):return None
        profile=obj['profile']
        if len(profile['rows'])<2:return None
        return profile
    except (OSError,KeyError,TypeError,ValueError):return None

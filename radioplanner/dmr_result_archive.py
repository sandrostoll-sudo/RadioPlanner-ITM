"""Versioned, bounded, portable DMR area result archive."""
import json
import os
import tempfile
from pathlib import Path

FORMAT = "RadioPlanner-DMR-results"
VERSION = 1
MAX_RESULTS = 30
MAX_CELLS = 50000


def validate(results):
    if not isinstance(results, list) or len(results) > MAX_RESULTS:
        raise ValueError("Ungültige Anzahl gespeicherter DMR-Berechnungen.")
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('cells'), list):
            raise ValueError("Ungültiger DMR-Ergebniseintrag.")
        if len(item['cells']) > MAX_CELLS or not isinstance(item.get('metadata'), dict):
            raise ValueError("Ungültige DMR-Rasterdaten.")
        for cell in item['cells']:
            if not isinstance(cell, dict) or not all(isinstance(cell.get(k), (int,float)) for k in ('south','north','west','east','dbm')):
                raise ValueError("Ungültige DMR-Rasterzelle.")
    return results


def path_for(folder, project_id):
    return Path(folder) / ('dmr_results_' + str(int(project_id)) + '.json')


def load(folder, project_id):
    path = path_for(folder, project_id)
    if not path.exists():
        return []
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('DMR-Ergebnisarchiv ist zu groß.')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('format') != FORMAT or data.get('version') != VERSION:
        raise ValueError('Unbekanntes DMR-Ergebnisarchiv.')
    return validate(data['results'])


def save(folder, project_id, results):
    validate(results)
    path=path_for(folder, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.dmr-results-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump({'format':FORMAT,'version':VERSION,'results':results},f,ensure_ascii=False,allow_nan=False)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

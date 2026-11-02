"""Portable, versioned single-project .rplan files (JSON, not executable)."""
import json
import os
import tempfile
from pathlib import Path

FORMAT = 'RadioPlanner-project'
VERSION = 1
TABLES = ('stations', 'radio_links', 'sirens', 'dmr_repeaters', 'dmr_devices')


def export_project(db, project_id, destination, profile_folder=None):
    conn = db.conn
    project = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if project is None:
        raise ValueError('Projekt nicht gefunden.')
    data = {'format': FORMAT, 'version': VERSION, 'project': dict(project)}
    for table in TABLES:
        data[table] = [dict(row) for row in conn.execute(
            f'SELECT * FROM {table} WHERE project_id=? ORDER BY id', (project_id,))]
    model_ids = sorted({s['siren_model_id'] for s in data['sirens'] if s.get('siren_model_id') is not None})
    data['siren_models'] = [dict(row) for mid in model_ids for row in
                            conn.execute('SELECT * FROM siren_models WHERE id=?', (mid,))]
    if profile_folder is not None:
        from .profile_report_cache import read_profile
        stations = {row['id']: row for row in data['stations']}
        data['link_profiles'] = {}
        from .analysis_cache import read_analysis
        data['link_analyses'] = {}
        for link in data['radio_links']:
            profile = read_profile(profile_folder, project_id, link, stations)
            if profile is not None:
                data['link_profiles'][str(link['id'])] = profile
            analysis = read_analysis(profile_folder, project_id, link, stations)
            if analysis is not None:
                data['link_analyses'][str(link['id'])] = analysis
    if profile_folder is not None:
        from .dmr_result_archive import load
        data['dmr_area_results'] = load(profile_folder, project_id)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.radioplanner-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def _insert(conn, table, row, exclude=('id',)):
    columns = [key for key in row if key not in exclude]
    allowed = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
    if not set(columns).issubset(allowed):
        raise ValueError(f'Unbekannte Felder in {table}: {set(columns) - allowed}')
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
    return conn.execute(sql, [row[k] for k in columns]).lastrowid


def import_project(db, source, profile_folder=None):
    path = Path(source)
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('Projektdatei ist zu groß.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format') != FORMAT or data.get('version') != VERSION:
        raise ValueError('Ungültiges oder nicht unterstütztes .rplan-Format.')
    if not isinstance(data.get('project'), dict) or any(not isinstance(data.get(t), list) for t in ('stations', 'radio_links', 'sirens', 'siren_models')) or not isinstance(data.get('dmr_repeaters', []), list) or not isinstance(data.get('dmr_devices', []), list):
        raise ValueError('Projektdatei ist unvollständig.')
    if len(data['stations']) > 100 or any(len(data[t]) > 10000 for t in ('sirens', 'radio_links', 'siren_models')) or len(data.get('dmr_repeaters', [])) > 1000 or len(data.get('dmr_devices', [])) > 10000:
        raise ValueError('Projekt enthält zu viele Einträge.')
    conn = db.conn
    try:
        conn.execute('BEGIN')
        project = dict(data['project'])
        project.pop('id', None)
        project['name'] = str(project.get('name', 'Importiertes Projekt'))
        project_id = _insert(conn, 'projects', project)
        station_map = {}
        for station in data['stations']:
            old_id = station['id']
            row = dict(station)
            row['project_id'] = project_id
            station_map[old_id] = _insert(conn, 'stations', row)
        model_map = {}
        for model in data['siren_models']:
            model_map[model['id']] = _insert(conn, 'siren_models', dict(model))
        for siren in data['sirens']:
            row = dict(siren)
            row['project_id'] = project_id
            if row.get('siren_model_id') is not None:
                row['siren_model_id'] = model_map[row['siren_model_id']]
            _insert(conn, 'sirens', row)
        for repeater in data.get('dmr_repeaters', []):
            row=dict(repeater);row['project_id']=project_id
            _insert(conn,'dmr_repeaters',row)
        for device in data.get('dmr_devices', []):
            row=dict(device);row['project_id']=project_id
            _insert(conn,'dmr_devices',row)
        link_map = {}
        for link in data['radio_links']:
            row = dict(link)
            row['project_id'] = project_id
            row['station_a_id'] = station_map[row['station_a_id']]
            row['station_b_id'] = station_map[row['station_b_id']]
            link_map[link['id']] = _insert(conn, 'radio_links', row)
        conn.commit()
        if profile_folder is not None:
            from .profile_report_cache import save_profile
            imported_profiles = data.get('link_profiles', {})
            if isinstance(imported_profiles, dict):
                station_rows = {r['id']: dict(r) for r in db.stations(project_id)}
                link_rows = {r['id']: dict(r) for r in db.radio_links(project_id)}
                from .analysis_cache import save_analysis
                imported_analyses=data.get('link_analyses',{})
                if not isinstance(imported_analyses,dict): imported_analyses={}
                for old_link_id,new_link_id in link_map.items():
                    analysis=imported_analyses.get(str(old_link_id))
                    if isinstance(analysis,dict):
                        link=link_rows[new_link_id]
                        try:
                            save_analysis(profile_folder,project_id,link,station_rows,analysis)
                        except (OSError,ValueError,TypeError,KeyError):pass
                for old_link_id, new_link_id in link_map.items():
                    profile = imported_profiles.get(str(old_link_id))
                    if not isinstance(profile, dict) or not isinstance(profile.get('rows'), list) or len(profile['rows']) < 2:
                        continue
                    link = link_rows[new_link_id]
                    try:
                        save_profile(profile_folder, project_id, link,
                                     station_rows[link['station_a_id']], station_rows[link['station_b_id']], profile)
                        # Restore the derived assessment too: old project files may
                        # contain profile samples but blank summary columns.
                        clearance=profile.get('min_clearance_m')
                        fresnel=profile.get('min_fresnel_60_m')
                        if isinstance(clearance,(int,float)) and isinstance(fresnel,(int,float)):
                            status='red' if clearance < 0 else 'yellow' if fresnel < 0 else 'green'
                            db.save_terrain_assessment(project_id,new_link_id,status,profile)
                    except (OSError, ValueError, TypeError, KeyError):
                        pass  # Project data remains usable if an optional profile cannot be restored.
        if profile_folder is not None and 'dmr_area_results' in data:
            from .dmr_result_archive import save, validate
            save(profile_folder, project_id, validate(data['dmr_area_results']))
        return project_id
    except Exception:
        conn.rollback()
        raise

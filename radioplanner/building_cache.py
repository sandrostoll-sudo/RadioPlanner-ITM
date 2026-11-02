"""Shared OSM building tile storage and safe cache management."""
from pathlib import Path
from datetime import datetime
import os
import shutil


def shared_cache_dir():
    # Independent of version-specific Qt AppDataLocation and extraction directory.
    if os.name == 'nt':
        base=Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'AppData'/'Local'))
    else:
        base=Path(os.environ.get('XDG_DATA_HOME') or (Path.home()/'.local'/'share'))
    path=base/'RadioPlanner'/'OSM_Cache'
    path.mkdir(parents=True,exist_ok=True)
    return path


def _source_dir(folder):
    folder=Path(folder)
    for candidate in (folder/'buildings',folder/'terrain_cache'/'buildings',folder/'OSM_Cache'/'buildings',folder/'radioplanner'/'terrain_cache'/'buildings'):
        if candidate.is_dir():return candidate
    return None


def import_legacy_cache(source, destination):
    source=_source_dir(source)
    if source is None:return 0
    target=Path(destination)/'buildings'
    target.mkdir(parents=True,exist_ok=True)
    count=0
    for entry in source.glob('*.json'):
        if not entry.is_file() or not (len(entry.stem)==64 and all(c in '0123456789abcdef' for c in entry.stem)):
            continue
        dest=target/entry.name
        if not dest.exists():
            shutil.copy2(entry,dest)
            count+=1
    return count


def migrate_legacy_cache(source,destination):
    return import_legacy_cache(source,destination)


def cache_entries(folder):
    directory=Path(folder)/'buildings'
    if not directory.is_dir():return []
    result=[]
    for p in sorted(directory.glob('*.json')):
        if len(p.stem)!=64 or any(c not in '0123456789abcdef' for c in p.stem):continue
        stat=p.stat()
        result.append({'name':p.name,'size_kb':stat.st_size/1024,'downloaded':datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')})
    return result


def delete_cache_entries(folder,names):
    directory=Path(folder)/'buildings'
    count=0
    for name in names:
        if Path(name).name!=name or not name.endswith('.json'):continue
        if len(name)!=69 or any(c not in '0123456789abcdef' for c in name[:-5]):continue
        target=directory/name
        if target.is_file():target.unlink();count+=1
    return count

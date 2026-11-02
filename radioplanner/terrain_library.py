"""Shared offline terrain dataset storage for all planning modules.

Only user-approved HTTPS GeoTIFF sources are downloaded. No assumption of
licensing, vertical datum, or authoritative precision is made.
"""
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request
from .tls import secure_urlopen
import shutil
import uuid

MAX_BYTES = 1024 * 1024 * 1024

def dataset_files(folder):
    root = Path(folder)
    return sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.tif', '.tiff')) if root.exists() else []

def import_geotiff(source, folder):
    src = Path(source)
    if not src.is_file() or src.suffix.lower() not in ('.tif', '.tiff'):
        raise ValueError('Bitte eine vorhandene GeoTIFF-Datei (.tif/.tiff) auswählen.')
    if src.stat().st_size > MAX_BYTES:
        raise ValueError('Datei über 1 GiB: Bitte große Datensätze direkt im Datenordner ablegen.')
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / src.name
    if target.resolve() == src.resolve():
        return target
    if target.exists():
        raise FileExistsError('Dateiname bereits vorhanden: ' + target.name)
    temp = target_dir / ('.import-' + uuid.uuid4().hex + '.tmp')
    try:
        with src.open('rb') as inp, temp.open('wb') as out:
            shutil.copyfileobj(inp, out)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return target

def download_geotiff(url, folder, progress=None, cancelled=None):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Nur direkte HTTPS-Links zu GeoTIFF-Dateien werden unterstützt.')
    filename = Path(parsed.path).name
    if not filename.lower().endswith(('.tif', '.tiff')):
        raise ValueError('Der Download-Link muss direkt auf eine .tif- oder .tiff-Datei zeigen.')
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    if target.exists():
        raise FileExistsError('Datei bereits vorhanden: ' + filename)
    temp = target_dir / ('.download-' + uuid.uuid4().hex + '.tmp')
    try:
        req = Request(url, headers={'User-Agent':'RadioPlanner/4.0 terrain-library'})
        with secure_urlopen(req, timeout=45) as response:
            final = urlparse(response.geturl())
            if final.scheme != 'https':
                raise ValueError('Unsichere Weiterleitung: HTTPS erforderlich.')
            size = int(response.headers.get('Content-Length', '0'))
            if size > MAX_BYTES:
                raise ValueError('Datei über 1 GiB; bitte manuell herunterladen.')
            total = 0
            with temp.open('wb') as out:
                while True:
                    if cancelled and cancelled():
                        raise InterruptedError('Download abgebrochen.')
                    chunk = response.read(128 * 1024)
                    if not chunk: break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError('Download überschreitet 1 GiB.')
                    out.write(chunk)
                    if progress: progress(total, size)
        if not total:
            raise ValueError('Leere Datei empfangen.')
        temp.replace(target)
        return target
    finally:
        temp.unlink(missing_ok=True)

def import_geotiffs(sources, folder, base_folder=None, progress=None, cancelled=None):
    """Import many terrain tiles, preserving relative directories when supplied.

    Existing matching paths are skipped, never overwritten. A source located
    inside the library is already available and is also skipped.
    """
    target_root = Path(folder).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    base = Path(base_folder).resolve() if base_folder else None
    items = [Path(p).resolve() for p in sources]
    stats = {'imported': 0, 'skipped': 0, 'total': len(items)}
    for index, src in enumerate(items, 1):
        if cancelled and cancelled():
            raise InterruptedError('Import abgebrochen.')
        if not src.is_file() or src.suffix.lower() not in ('.tif', '.tiff'):
            stats['skipped'] += 1
            continue
        if src.stat().st_size > MAX_BYTES:
            raise ValueError(f'Datei über 1 GiB: {src.name}. Große Dateien bitte direkt im Datenordner ablegen.')
        relative = src.relative_to(base) if base else Path(src.name)
        target = target_root / relative
        if target == src or target.exists():
            stats['skipped'] += 1
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.parent / ('.import-' + uuid.uuid4().hex + '.tmp')
            try:
                with src.open('rb') as inp, temp.open('wb') as out:
                    while True:
                        if cancelled and cancelled():
                            raise InterruptedError('Import abgebrochen.')
                        chunk = inp.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                temp.replace(target)
                stats['imported'] += 1
            finally:
                temp.unlink(missing_ok=True)
        if progress:
            progress(index, len(items))
    return stats

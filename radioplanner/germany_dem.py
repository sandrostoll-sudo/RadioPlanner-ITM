"""Optional nationwide Copernicus GLO-30 fallback (DSM, not official DGM1).

Public AWS-hosted COG tiles, one geographic degree each. Downloads require
explicit user confirmation; no network traffic during ordinary terrain sampling.
"""
import math
from pathlib import Path
from urllib.request import Request
from .tls import secure_urlopen
from urllib.error import HTTPError, URLError

BUCKET = 'https://copernicus-dem-30m.s3.amazonaws.com'


def tile_name(latitude, longitude):
    if not (-90 <= latitude < 90 and -180 <= longitude < 180):
        raise ValueError('Koordinate außerhalb des gültigen Bereichs')
    lat, lon = math.floor(latitude), math.floor(longitude)
    return f'Copernicus_DSM_COG_10_{"N" if lat >= 0 else "S"}{abs(lat):02d}_00_{"E" if lon >= 0 else "W"}{abs(lon):03d}_00_DEM.tif'


def tile_url(name):
    if not name.startswith('Copernicus_DSM_COG_10_') or not name.endswith('_DEM.tif') or '/' in name:
        raise ValueError('Ungültiger Copernicus-Kachelname')
    return f'{BUCKET}/{name[:-4]}/{name}'


def missing_tiles(points, folder):
    """One-degree tiles for uncovered sample coordinates; existing files are reused."""
    existing = {p.name.lower() for p in Path(folder).rglob('*.tif')} if Path(folder).exists() else set()
    return [(name, tile_url(name)) for name in sorted({tile_name(a,b) for a,b in points})
            if name.lower() not in existing]


def remote_size(url, timeout=12):
    """Return size in bytes, None if server does not disclose it; raise if unavailable."""
    req = Request(url, method='HEAD', headers={'User-Agent': 'RadioPlanner/4.0.5'})
    try:
        with secure_urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError(f'HTTP {response.status}')
            return int(response.headers['Content-Length']) if response.headers.get('Content-Length') else None
    except HTTPError as exc:
        raise ValueError(f'Datenkachel nicht verfügbar (HTTP {exc.code})') from exc
    except URLError as exc:
        raise ValueError(f'Datenquelle nicht erreichbar: {exc.reason}. Bei SSL-Zertifikatsfehlern: certifi installieren/aktualisieren und Windows-Zertifikate prüfen; Zertifikatsprüfung nicht deaktivieren.') from exc

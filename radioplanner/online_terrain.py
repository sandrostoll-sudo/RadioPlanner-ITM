"""Optional online elevation samples. Open-Meteo is NOT an official DGM1 source.
The service documents a 90 m elevation model; heights are not guaranteed NHN.
"""
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .terrain import TerrainError

URL = 'https://api.open-meteo.com/v1/elevation'
SOURCE = 'Open-Meteo Elevation API (ca. 90 m, Höhenbezug nicht verifiziert)'

def elevations(points, cache_folder):
    """Batch elevation requests with persistent partial cache and bounded 429 backoff."""
    import time
    from urllib.error import HTTPError
    if not points:
        return []
    folder = Path(cache_folder)
    folder.mkdir(parents=True, exist_ok=True)
    cachefile = folder / 'open_meteo_elevations.json'
    try:
        cache = json.loads(cachefile.read_text(encoding='utf-8')) if cachefile.exists() else {}
    except (OSError, ValueError):
        cache = {}
    # Keep existing cache key precision for backward compatibility.
    keys = [f'{lat:.6f},{lon:.6f}' for lat, lon in points]
    missing = list(dict.fromkeys(k for k in keys if k not in cache))

    def save_cache():
        try:
            temp = cachefile.with_suffix('.tmp')
            temp.write_text(json.dumps(cache), encoding='utf-8')
            temp.replace(cachefile)
        except OSError:
            pass

    for offset in range(0, len(missing), 100):
        batch = missing[offset:offset + 100]
        url = URL + '?' + urlencode({
            'latitude': ','.join(k.split(',')[0] for k in batch),
            'longitude': ','.join(k.split(',')[1] for k in batch)})
        for attempt in range(3):
            try:
                req = Request(url, headers={'User-Agent': 'RadioPlanner/3.9.1 (terrain profile)'})
                with urlopen(req, timeout=25) as response:
                    data = json.load(response)
                heights = data.get('elevation')
                if not isinstance(heights, list) or len(heights) != len(batch):
                    raise ValueError('Unerwartetes Antwortformat')
                validated = []
                for h in heights:
                    if h is None or not math.isfinite(float(h)):
                        raise ValueError('Ungültiger Höhenwert')
                    validated.append(float(h))
                cache.update(zip(batch, validated))
                save_cache()  # Preserve completed batches even when later requests fail.
                break
            except HTTPError as exc:
                if exc.code != 429:
                    raise TerrainError(f'Online-Höhenabfrage fehlgeschlagen (HTTP {exc.code}).') from exc
                if attempt == 2:
                    raise TerrainError('Höhendienst begrenzt die Anfragen (HTTP 429). Bereits geladene Höhen wurden gespeichert. Bitte später erneut versuchen oder lokale DGM-Daten verwenden.') from exc
                header = exc.headers.get('Retry-After') if exc.headers else None
                try:
                    delay = min(15.0, max(1.0, float(header))) if header else 3.0 * (attempt + 1)
                except ValueError:
                    delay = 3.0 * (attempt + 1)
                time.sleep(delay)
            except (OSError, ValueError, TypeError) as exc:
                raise TerrainError(f'Online-Höhenabfrage fehlgeschlagen: {exc}.') from exc
        if offset + 100 < len(missing):
            time.sleep(0.35)
    return [cache[k] for k in keys]

def sample_online(lat, lon, cache_folder):
    h = elevations([(lat, lon)], cache_folder)[0]
    return dict(height=h, source=SOURCE, resolution=90, crs='EPSG:4326', vertical_datum='Nicht geprüft (Online-Höhenmodell)')

"""Opt-in GeoTIFF catalogue. Never infer download URLs from tile names."""
import json
from pathlib import Path
from urllib.parse import urlparse


def load_catalog(path):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload,dict) or not isinstance(payload.get('tiles'),list):
        raise ValueError('Katalog muss ein JSON-Objekt mit Liste "tiles" sein.')
    tiles=[]
    for item in payload['tiles']:
        if not isinstance(item,dict):raise ValueError('Ungültiger Kacheleintrag.')
        url=item.get('url','')
        parsed=urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or not parsed.path.lower().endswith(('.tif','.tiff')):
            raise ValueError('Kacheladresse muss ein direkter HTTPS-Link auf GeoTIFF sein.')
        bbox=item.get('bbox')
        if not isinstance(bbox,list) or len(bbox)!=4 or not all(isinstance(v,(int,float)) and not isinstance(v,bool) for v in bbox):
            raise ValueError('bbox muss [West, Süd, Ost, Nord] in WGS84 sein.')
        w,s,e,n=bbox
        if not (-180<=w<e<=180 and -90<=s<n<=90):raise ValueError('Ungültige Kachelgrenzen.')
        size=item.get('size_bytes')
        if size is not None and (not isinstance(size,int) or isinstance(size,bool) or size<0):raise ValueError('Ungültige Dateigröße.')
        tiles.append({'url':url,'bbox':bbox,'size_bytes':size})
    return tiles


def matching_tiles(tiles, missing_coordinates):
    """Return unique catalogue entries intersecting any uncovered sample point."""
    return [t for t in tiles if any(t['bbox'][0]<=lon<=t['bbox'][2] and t['bbox'][1]<=lat<=t['bbox'][3]
                                   for lat,lon in missing_coordinates)]


def download_summary(tiles):
    known=sum(t['size_bytes'] for t in tiles if t['size_bytes'] is not None)
    unknown=sum(t['size_bytes'] is None for t in tiles)
    return f'{len(tiles)} Kachel(n), {known/1048576:.1f} MiB bekannte Größe' + (f', {unknown} ohne Größenangabe' if unknown else '')

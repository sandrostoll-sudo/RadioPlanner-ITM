"""Optional OSM building obstruction approximation; not an acoustic engineering model."""
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from .acoustic_grid import R


def _tile_bounds(south, west, north, east, step=0.10, max_tiles=100):
    """Split a requested region into Overpass-sized rectangles."""
    if not all(math.isfinite(v) for v in (south, west, north, east)) or south >= north or west >= east:
        raise ValueError('Ungültiges Untersuchungsgebiet für Gebäudedaten.')
    if south < -90 or north > 90 or west < -180 or east > 180:
        raise ValueError('Untersuchungsgebiet außerhalb gültiger Koordinaten.')
    rows = math.ceil((north-south)/step)
    cols = math.ceil((east-west)/step)
    if rows * cols > max_tiles:
        raise ValueError(f'Untersuchungsgebiet benötigt {rows*cols} OSM-Kacheln (Maximum {max_tiles}). Bitte Gebiet verkleinern oder Gebäudeabschattung deaktivieren.')
    for row in range(rows):
        for col in range(cols):
            yield (south+row*step, west+col*step,
                   min(north,south+(row+1)*step), min(east,west+(col+1)*step))


OVERPASS_ENDPOINTS = (
    'https://overpass.private.coffee/api/interpreter',
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
)


def _fetch_tile(bounds, cache_folder=None, mode='auto'):
    """Read persistent cache or download, depending on explicit user-selected mode."""
    if mode not in ('auto', 'offline', 'refresh'):
        raise ValueError('Unbekannter Gebäudedatenmodus.')
    south, west, north, east = bounds
    query=f'[out:json][timeout:35];way["building"]({south:.6f},{west:.6f},{north:.6f},{east:.6f});out geom;'
    path=None
    if cache_folder is not None:
        import hashlib
        cache=Path(cache_folder)/'buildings'
        cache.mkdir(parents=True,exist_ok=True)
        path=cache/(hashlib.sha256(query.encode()).hexdigest()+'.json')
        if mode != 'refresh' and path.exists():
            try:
                cached=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(cached,list):return cached
            except (ValueError,OSError):
                pass
    if mode == 'offline':
        raise RuntimeError('Kachel nicht im lokalen Gebäudecache vorhanden.')
    errors=[]
    for url in OVERPASS_ENDPOINTS:
        request=urllib.request.Request(url,data=urllib.parse.urlencode({'data':query}).encode(),
            headers={'User-Agent':'RadioPlanner/2.0.4 (OSM building planning prototype)'})
        try:
            with urllib.request.urlopen(request,timeout=45) as response:
                data=json.load(response)
            if not isinstance(data,dict) or not isinstance(data.get('elements'),list):
                raise ValueError('Ungültige Overpass-Antwort')
            break
        except Exception as exc:
            errors.append(f'{url}: {type(exc).__name__}: {exc}')
    else:
        raise RuntimeError('Kein Overpass-Server erreichbar. '+' | '.join(errors))
    result=[]
    for element in data['elements']:
        geom=element.get('geometry',[])
        if len(geom)<4:continue
        tags=element.get('tags',{})
        try:
            height=float(str(tags.get('height','')).replace('m','').strip())
        except (ValueError,TypeError):
            try:height=float(tags.get('building:levels',0))*3.0
            except (ValueError,TypeError):height=0
        result.append({'id':element.get('id'),'polygon':[[p['lat'],p['lon']] for p in geom],
                       'height_m':height if math.isfinite(height) and height>0 else None})
    if path is not None:
        import os, tempfile
        fd,tmp=tempfile.mkstemp(prefix='osm_',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump(result,f)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return result


def fetch_buildings(south, west, north, east, cache_folder=None, max_buildings=25000,
                    progress=None, warnings=None, cancel=None, mode='auto'):
    """Fetch building outlines in tiles, cache each tile, deduplicate OSM IDs.

    Failed tiles are skipped only if at least one tile succeeded; warnings records
    incomplete coverage so callers can avoid interpreting gaps as no buildings.
    """
    tiles=list(_tile_bounds(south,west,north,east))
    result=[]
    seen=set()
    failed=0
    failures=[]
    for index,bounds in enumerate(tiles,1):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Berechnung abgebrochen.")
        if progress is not None:progress(index,len(tiles))
        try:
            items=_fetch_tile(bounds,cache_folder,mode=mode)
        except (RuntimeError, OSError, ValueError) as exc:
            failures.append(str(exc))
            failed+=1
            continue
        for item in items:
            key=item.get('id')
            if key is not None and key in seen:continue
            if key is not None:seen.add(key)
            result.append(item)
            if len(result)>max_buildings:
                raise ValueError(f'Mehr als {max_buildings} Gebäude gefunden. Untersuchungsgebiet verkleinern.')
    if failed == len(tiles):
        raise RuntimeError(('Keine Gebäudekacheln lokal gespeichert.' if mode=='offline' else 'Alle OSM-Gebäudekacheln konnten nicht geladen werden.') + (' Ursache: '+failures[0] if failures else ''))
    if failed and warnings is not None:
        warnings.append(f'{failed} von {len(tiles)} OSM-Kacheln konnten nicht geladen werden. Gebäudeabschattung ist dort unvollständig.')
    return result


def _inside(x,y,points):
    inside=False
    for i in range(len(points)):
        x1,y1=points[i-1];x2,y2=points[i]
        if (y1>y)!=(y2>y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1:inside=not inside
    return inside


def _cross(a,b,c,d):
    def orient(p,q,r):return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    o1,o2,o3,o4=orient(a,b,c),orient(a,b,d),orient(c,d,a),orient(c,d,b)
    return o1*o2<0 and o3*o4<0


class BuildingScreen:
    """Approximate direct-path screening using 2D outlines and height assumptions."""
    def __init__(self, buildings, default_height_m=10, loss_db=8, source_height_m=8, receiver_height_m=1.5):
        self.default_height_m=float(default_height_m)
        self.loss_db=float(loss_db)
        self.receiver_height_m=float(receiver_height_m)
        self.source_height_m=float(source_height_m)
        self.buildings=[]
        self._bins={}
        self._cell=0.003
        for b in buildings:
            poly=b.get('polygon',[])
            if len(poly)<3:continue
            height=float(b.get('height_m') or self.default_height_m)
            lats=[v[0] for v in poly];lons=[v[1] for v in poly]
            box=(min(lats),min(lons),max(lats),max(lons))
            index=len(self.buildings)
            self.buildings.append((poly,height,box))
            x0=int(math.floor(box[1]/self._cell));x1=int(math.floor(box[3]/self._cell))
            y0=int(math.floor(box[0]/self._cell));y1=int(math.floor(box[2]/self._cell))
            for x in range(x0,x1+1):
                for y in range(y0,y1+1):self._bins.setdefault((x,y),[]).append(index)

    def penalty(self, lat1,lon1,lat2,lon2,source_height_m=None):
        coslat=max(.15,math.cos(math.radians((lat1+lat2)/2)))
        scale_x=math.pi/180*R*coslat;scale_y=math.pi/180*R
        endpoint=((lon2-lon1)*scale_x,(lat2-lat1)*scale_y)
        distance=math.hypot(*endpoint)
        if distance<2:return 0.0
        a=(0.,0.);b=endpoint
        source_h=self.source_height_m if source_height_m is None else float(source_height_m)
        minlat,maxlat=sorted((lat1,lat2));minlon,maxlon=sorted((lon1,lon2))
        x0=int(math.floor(minlon/self._cell));x1=int(math.floor(maxlon/self._cell))
        y0=int(math.floor(minlat/self._cell));y1=int(math.floor(maxlat/self._cell))
        candidates=set()
        for x in range(x0,x1+1):
            for y in range(y0,y1+1):candidates.update(self._bins.get((x,y),()))
        for index in candidates:
            poly,height,box=self.buildings[index]
            if box[2]<minlat or box[0]>maxlat or box[3]<minlon or box[1]>maxlon:continue
            pts=[((lon-lon1)*scale_x,(lat-lat1)*scale_y) for lat,lon in poly]
            # Exclude the building directly containing the source or receiver.
            if _inside(*a,pts) or _inside(*b,pts):continue
            for i in range(len(pts)):
                c,d=pts[i-1],pts[i]
                if _cross(a,b,c,d):
                    # Conservative approximate height test at the intersected segment.
                    midpoint=((c[0]+d[0])/2,(c[1]+d[1])/2)
                    t=max(0.,min(1.,(midpoint[0]*b[0]+midpoint[1]*b[1])/(distance*distance)))
                    sight=source_h+(self.receiver_height_m-source_h)*t
                    if height>sight:return self.loss_db
        return 0.0

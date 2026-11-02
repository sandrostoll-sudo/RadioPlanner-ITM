"""Terrain-based path analysis. Heights must share a verified vertical datum."""
import math
from pathlib import Path
from .terrain import TerrainError

R_EARTH = 6371008.8

def distance_m(a, b):
    p1,p2=math.radians(a[0]),math.radians(b[0]); dl=math.radians(b[1]-a[1])
    x=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R_EARTH*math.asin(min(1,math.sqrt(x)))

def antenna_alignment(a, b, height_a_m=0.0, height_b_m=0.0, ground_a_m=None, ground_b_m=None, k=4/3):
    """Initial great-circle bearings and approximate vertical angles (degrees).

    Elevation angles require terrain heights with a common vertical datum.
    The effective-Earth approximation is consistent with the profile model.
    """
    if a == b or k <= 0:
        raise ValueError('Unterschiedliche Standorte und positiver k-Faktor erforderlich.')
    def bearing(start, end):
        lat1,lon1=map(math.radians,start)
        lat2,lon2=map(math.radians,end)
        dl=lon2-lon1
        y=math.sin(dl)*math.cos(lat2)
        x=math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(dl)
        return math.degrees(math.atan2(y,x))%360
    d=distance_m(a,b)
    result={'azimuth_a_deg':bearing(a,b),'azimuth_b_deg':bearing(b,a),
            'distance_km':d/1000,'k_factor':k,
            'earth_bulge_mid_m':d*d/(8*k*R_EARTH)}
    if ground_a_m is not None and ground_b_m is not None:
        top_a=ground_a_m+height_a_m
        top_b=ground_b_m+height_b_m
        # The local horizon drops by d/(2*k*R) radians at each endpoint.
        drop_deg=math.degrees(d/(2*k*R_EARTH))
        result['elevation_a_deg']=math.degrees(math.atan2(top_b-top_a,d))-drop_deg
        result['elevation_b_deg']=math.degrees(math.atan2(top_a-top_b,d))-drop_deg
    return result

def analyze(folder, a, b, height_a, height_b, frequency_mhz, k=4/3, samples=301, online_cache=None, fresnel_fraction=0.6):
    if online_cache is not None:
        return analyze_online(a,b,height_a,height_b,frequency_mhz,k,samples,online_cache,fresnel_fraction)
    if not 0 <= fresnel_fraction <= 1: raise TerrainError('Fresnel-Freihaltung muss zwischen 0 und 100 % liegen.')
    try:
        import rasterio
        from rasterio.warp import transform
    except ImportError as exc:
        raise TerrainError('Bitte rasterio installieren: py -3.12 -m pip install -r requirements.txt') from exc
    if not folder or not Path(folder).is_dir():
        raise TerrainError('Bitte einen Ordner mit GeoTIFF-Geländedaten auswählen.')
    if a==b: raise TerrainError('Die Standorte müssen unterschiedliche Koordinaten haben.')
    if frequency_mhz<=0 or k<=0: raise TerrainError('Frequenz und k-Faktor müssen positiv sein.')
    paths=sorted(p for p in Path(folder).rglob('*') if p.suffix.lower() in ('.tif','.tiff'))
    if not paths: raise TerrainError('Keine GeoTIFF-Dateien gefunden.')
    from contextlib import ExitStack
    with ExitStack() as stack:
        datasets=[]
        for path in paths:
            try:
                ds=stack.enter_context(rasterio.open(path))
                if ds.crs is not None and ds.count:
                    res=max(abs(ds.res[0]),abs(ds.res[1])) if ds.crs.is_projected else float('inf')
                    datasets.append((res,ds,path))
            except (OSError,ValueError): continue
        datasets.sort(key=lambda item:item[0])
        if not datasets: raise TerrainError('Keine verwendbaren georeferenzierten Raster gefunden.')
        # Great-circle interpolation, rather than interpolating degrees on long routes.
        p1,p2=map(math.radians,(a[0],b[0])); l1,l2=map(math.radians,(a[1],b[1]))
        def xyz(p,l): return (math.cos(p)*math.cos(l),math.cos(p)*math.sin(l),math.sin(p))
        v1,v2=xyz(p1,l1),xyz(p2,l2)
        angle=math.acos(max(-1,min(1,sum(x*y for x,y in zip(v1,v2)))))
        def coordinate(t):
            if angle<1e-9: return a
            w1=math.sin((1-t)*angle)/math.sin(angle);w2=math.sin(t*angle)/math.sin(angle)
            x,y,z=(w1*v1[i]+w2*v2[i] for i in range(3))
            return math.degrees(math.atan2(z,math.hypot(x,y))),math.degrees(math.atan2(y,x))
        points=[coordinate(i/(samples-1)) for i in range(samples)]
        heights=[None]*samples; sources=[None]*samples
        # Transform coordinate vectors once per raster, not once per sample.
        for _,ds,path in datasets:
            lons=[p[1] for p in points];lats=[p[0] for p in points]
            try: xs,ys=transform('EPSG:4326',ds.crs,lons,lats)
            except Exception: continue
            for i,(x,y) in enumerate(zip(xs,ys)):
                if heights[i] is not None: continue
                if not (ds.bounds.left<=x<ds.bounds.right and ds.bounds.bottom<y<=ds.bounds.top):continue
                row,col=ds.index(x,y)
                if not (0<=row<ds.height and 0<=col<ds.width):continue
                try: pixel=ds.read(1,window=((row,row+1),(col,col+1)),masked=True)[0,0]
                except Exception:continue
                if bool(getattr(pixel,'mask',False)):continue
                h=float(pixel)
                if math.isfinite(h):heights[i]=h;sources[i]=str(path)
    if any(h is None for h in heights):
        missing=sum(h is None for h in heights)
        raise TerrainError(f'Geländeprofil unvollständig: {missing} von {samples} Punkten ohne DGM-Höhe. Weitere GeoTIFF-Kacheln laden.')
    # Do not silently mix vertical references across datasets.
    if len(set(sources))>1:
        raise TerrainError('Das Profil benötigt mehrere Raster. Bitte zuerst sicherstellen, dass alle dasselbe vertikale Höhenbezugssystem besitzen; automatischer Mischbetrieb ist gesperrt.')
    d=distance_m(a,b)
    start=heights[0]+height_a; end=heights[-1]+height_b
    rows=[];min_clear=float('inf');min_fresnel=float('inf')
    wavelength=299792458/(frequency_mhz*1e6)
    for i,h in enumerate(heights):
        t=i/(samples-1);d1=d*t;d2=d-d1
        bulge=d1*d2/(2*k*R_EARTH)
        line=start+(end-start)*t
        r=math.sqrt(wavelength*d1*d2/d) if d else 0
        clearance=line-(h+bulge)
        rows.append(dict(distance_km=d1/1000,terrain_m=h,curved_terrain_m=h+bulge,line_m=line,fresnel_radius_m=r,clearance_m=clearance))
        min_clear=min(min_clear,clearance);min_fresnel=min(min_fresnel,clearance-fresnel_fraction*r)
    return dict(rows=rows,distance_km=d/1000,min_clearance_m=min_clear,min_fresnel_60_m=min_fresnel,
                frequency_mhz=frequency_mhz,k=k,fresnel_fraction=fresnel_fraction,source=sources[0],vertical_datum='Nicht geprüft: Quelle vor fachlicher Nutzung kontrollieren',
                assessment=('Gelände blockiert Sichtlinie' if min_clear<0 else f'{fresnel_fraction*100:g} % Fresnelzone nicht frei' if min_fresnel<0 else f'Geländeseitig {fresnel_fraction*100:g} % Fresnelzone frei'))


def analyze_online(a,b,height_a,height_b,frequency_mhz,k,samples,cache_folder,fresnel_fraction=0.6):
    """Approximate online terrain profile; not an official high-resolution DGM."""
    from .online_terrain import elevations, SOURCE
    if a==b or frequency_mhz<=0 or k<=0 or samples<2 or not 0<=fresnel_fraction<=1:
        raise TerrainError('Ungültige Parameter für die Profilberechnung.')
    # Linear interpolation of coordinates is sufficient for the regional online preview.
    points=[(a[0]+(b[0]-a[0])*i/(samples-1),a[1]+(b[1]-a[1])*i/(samples-1)) for i in range(samples)]
    heights=elevations(points,cache_folder)
    d=distance_m(a,b)
    start=heights[0]+height_a;end=heights[-1]+height_b
    wavelength=299792458/(frequency_mhz*1e6)
    rows=[];min_clear=float('inf');min_fresnel=float('inf')
    for i,h in enumerate(heights):
        t=i/(samples-1);d1=d*t;d2=d-d1
        bulge=d1*d2/(2*k*R_EARTH)
        line=start+(end-start)*t
        radius=math.sqrt(wavelength*d1*d2/d)
        clearance=line-(h+bulge)
        rows.append(dict(distance_km=d1/1000,terrain_m=h,curved_terrain_m=h+bulge,line_m=line,fresnel_radius_m=radius,clearance_m=clearance))
        min_clear=min(min_clear,clearance);min_fresnel=min(min_fresnel,clearance-fresnel_fraction*radius)
    return dict(rows=rows,distance_km=d/1000,min_clearance_m=min_clear,min_fresnel_60_m=min_fresnel,
                frequency_mhz=frequency_mhz,k=k,fresnel_fraction=fresnel_fraction,source=SOURCE,vertical_datum='Ungeprüft; keine DGM1-Präzisionsanalyse',
                assessment=('Gelände blockiert Sichtlinie' if min_clear<0 else f'{fresnel_fraction*100:g} % Fresnelzone nicht frei' if min_fresnel<0 else f'Geländeseitig {fresnel_fraction*100:g} % Fresnelzone frei')+' (Online-Modell: nur vorläufig)')


def great_circle_points(a,b,samples,linear=False):
    """Return the same coordinates used by the local or online elevation sampler."""
    if samples<2:raise ValueError('Mindestens zwei Profilpunkte erforderlich.')
    if linear:return [(a[0]+(b[0]-a[0])*i/(samples-1),a[1]+(b[1]-a[1])*i/(samples-1)) for i in range(samples)]
    p1,p2=map(math.radians,(a[0],b[0]));l1,l2=map(math.radians,(a[1],b[1]))
    def xyz(p,l):return (math.cos(p)*math.cos(l),math.cos(p)*math.sin(l),math.sin(p))
    v1,v2=xyz(p1,l1),xyz(p2,l2)
    angle=math.acos(max(-1,min(1,sum(x*y for x,y in zip(v1,v2)))))
    if angle<1e-9:return [a]*samples
    result=[]
    for i in range(samples):
        t=i/(samples-1);w1=math.sin((1-t)*angle)/math.sin(angle);w2=math.sin(t*angle)/math.sin(angle)
        x,y,z=(w1*v1[j]+w2*v2[j] for j in range(3))
        result.append((math.degrees(math.atan2(z,math.hypot(x,y))),math.degrees(math.atan2(y,x))))
    return result


def required_antenna_raises(profile):
    """Minimum nonnegative mast raises (m) for A-only, B-only, or equal raises.

    Uses the sampled terrain, effective-earth curvature and requested Fresnel
    fraction already present in the computed profile. At the exact endpoints
    the ground sample is excluded: its obstruction is the station itself.
    """
    rows = profile['rows']
    if len(rows) < 3:
        raise ValueError('Mindestens drei Profilpunkte erforderlich.')
    fraction = profile['fresnel_fraction']
    a_only = b_only = both = 0.0
    worst = None
    for i in range(1, len(rows)-1):
        r = rows[i]
        t = i / (len(rows)-1)
        deficit = fraction * r['fresnel_radius_m'] - r['clearance_m']
        if worst is None or deficit > worst[0]:
            worst = (deficit, i)
        if deficit > 0:
            a_only = max(a_only, deficit/(1-t))
            b_only = max(b_only, deficit/t)
            both = max(both, deficit)
    return dict(raise_a_only_m=a_only, raise_b_only_m=b_only,
                raise_both_each_m=both, critical_index=worst[1],
                critical_deficit_m=max(0.0, worst[0]))

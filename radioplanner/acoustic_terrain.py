"""Exploratory terrain-screening penalty, not a validated diffraction model."""
import math
from .online_terrain import elevations, SOURCE
from .terrain import sample_folder, TerrainError


def terrain_screen(sirens, cache_folder=None, dem_folder=None, bearings=24, steps=12, max_range_m=4000):
    """Sample radial paths; return per-siren directional screening lookup.

    Uses an approximate line-of-sight slope comparison; penalty is an explicitly
    illustrative 0..15 dB and must not be used for warning-system certification.
    """
    if not cache_folder and not dem_folder:
        raise TerrainError('Bitte eine Höhendatenquelle wählen.')
    coords=[]
    for s in sirens:
        lat=float(s['latitude']);lon=float(s['longitude']); cl=max(.1,math.cos(math.radians(lat)))
        for b in range(bearings):
            a=2*math.pi*b/bearings
            for k in range(steps+1):
                r=max_range_m*k/steps
                coords.append((lat+math.cos(a)*r/111195, lon+math.sin(a)*r/(111195*cl)))
    if dem_folder:
        hs=[sample_folder(dem_folder,lat,lon)['height'] for lat,lon in coords]
        source='Lokale GeoTIFF-Dateien (Höhenbezug prüfen)'
    else:
        hs=elevations(coords,cache_folder)
        source=SOURCE
    result=[];idx=0
    for s in sirens:
        radial=[]
        for b in range(bearings):
            h=hs[idx:idx+steps+1];idx+=steps+1
            source_z=(float(s['terrain_height_m']) if s.get('terrain_height_m') is not None else h[0])+float(s.get('mounting_height_m') or 0)
            penalties=[]
            for k in range(steps+1):
                if k==0:
                    penalties.append(0.0);continue
                r=max_range_m*k/steps
                target_z=h[k]+1.5
                line_slope=(target_z-source_z)/r
                max_excess=0.0
                for m in range(1,k):
                    rm=max_range_m*m/steps
                    expected=source_z+line_slope*rm
                    max_excess=max(max_excess,h[m]-expected)
                # Schematic attenuation, capped. Not an ISO 9613-2 calculation.
                penalties.append(min(15.0,max_excess*.75))
            radial.append(penalties)
        result.append(radial)
    return {'penalties':result,'bearings':bearings,'steps':steps,'max_range_m':max_range_m,'source':source}


def penalty_at(screen, siren_index, bearing_deg, distance_m):
    if not screen:return 0.0
    bearings=screen['bearings'];steps=screen['steps']
    b=int(round((bearing_deg%360)*bearings/360))%bearings
    k=min(steps,max(0,int(round(distance_m/screen['max_range_m']*steps))))
    return screen['penalties'][siren_index][b][k]

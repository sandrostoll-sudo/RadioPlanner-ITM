"""Grid-based illustrative area coverage analysis, not a validated siren forecast."""
import base64
import math
import struct
import zlib
from .acoustic_grid import R, level_at


def point_in_polygon(lat, lon, polygon):
    inside=False
    for i in range(len(polygon)):
        a=polygon[i];b=polygon[(i+1)%len(polygon)]
        if (a[0]>lat)!=(b[0]>lat):
            crossing=(b[1]-a[1])*(lat-a[0])/(b[0]-a[0])+a[1]
            if lon<crossing:inside=not inside
    return inside


def analyze(sirens, center_lat, center_lon, width_km, height_km, minimum_db=70,
            reference_db=120, attenuation=0.005, terrain_screen=None, cells=100, polygon=None, building_screen=None, cancel=None):
    if not sirens:
        raise ValueError('Mindestens eine Sirene erforderlich.')
    if not (-85 < center_lat < 85 and -180 <= center_lon <= 180):
        raise ValueError('Ungültiger Mittelpunkt.')
    if not (0 < width_km <= 50 and 0 < height_km <= 50):
        raise ValueError('Breite und Höhe müssen zwischen 0 und 50 km liegen.')
    coslat=math.cos(math.radians(center_lat))
    lat_half=height_km*500/R*180/math.pi
    lon_half=width_km*500/(R*coslat)*180/math.pi
    bounds=[[center_lat-lat_half,center_lon-lon_half],[center_lat+lat_half,center_lon+lon_half]]
    nx=int(cells)
    ny=max(20,min(250,round(nx*height_km/width_km)))
    rows=[]
    served=0
    included=0
    for j in range(ny):
        if cancel is not None and cancel.is_set():raise InterruptedError()
        lat=bounds[1][0]-(j+0.5)*(bounds[1][0]-bounds[0][0])/ny
        row=bytearray([0])
        for i in range(nx):
            lon=bounds[0][1]+(i+0.5)*(bounds[1][1]-bounds[0][1])/nx
            if polygon is not None and not point_in_polygon(lat,lon,polygon):
                row.extend((0,0,0,0))
                continue
            included+=1
            db=level_at(sirens,lat,lon,reference_db,attenuation,terrain_screen,building_screen)['level_db']
            good=db is not None and db>=minimum_db
            served+=bool(good)
            row.extend((35,160,75,105) if good else (220,50,55,155))
        rows.append(bytes(row))
    def chunk(tag,data):
        return struct.pack('!I',len(data))+tag+data+struct.pack('!I',zlib.crc32(tag+data)&0xffffffff)
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',nx,ny,8,6,0,0,0))+
         chunk(b'IDAT',zlib.compress(b''.join(rows),6))+chunk(b'IEND',b''))
    total=width_km*height_km
    fraction=served/included if included else 0
    total*=included/(nx*ny)
    return {'image':'data:image/png;base64,'+base64.b64encode(png).decode('ascii'),
            'bounds':bounds,'threshold':float(minimum_db),'analysis_mode':'gaps',
            'area_km2':total,'served_km2':total*fraction,'gap_km2':total*(1-fraction),
            'served_percent':100*fraction,'gap_percent':100*(1-fraction),
            'grid_width':nx,'grid_height':ny,'sampled_cells':included}

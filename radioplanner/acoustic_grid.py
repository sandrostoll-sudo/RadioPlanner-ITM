"""Illustrative acoustic raster; NOT a validated outdoor propagation model."""
import base64
import math
import struct
import zlib

R = 6371008.8

def direction_gain(pattern, azimuth, horns, bearing, horn_azimuths=None):
    """Smooth schematic attenuation in dB, not manufacturer directivity data."""
    count = max(1, min(32, int(horns or 1)))
    p = (pattern or '').lower()
    if 'omni' in p:
        return 0.0
    angles = [azimuth]
    if 'bidir' in p:
        angles.append(azimuth + 180)
    elif 'individ' in p:
        angles = parse_horn_azimuths(horn_azimuths, count, azimuth)
    delta = min(abs((bearing-a+180)%360-180) for a in angles)
    # 0 dB on-axis; smooth rolloff with 18 dB maximum attenuation.
    return -18.0 * min(1.0, (delta/90.0)**1.6)

def parse_horn_azimuths(value, count, azimuth=0):
    """Explicit horn bearings; old records fall back to evenly spaced horns."""
    import json
    try:
        parsed=json.loads(value) if isinstance(value,str) else value
        if isinstance(parsed,list) and len(parsed)==count and all(isinstance(x,(int,float)) and 0<=x<360 for x in parsed):
            return [float(x) for x in parsed]
    except (ValueError,TypeError):
        pass
    return [(float(azimuth)+360*i/count)%360 for i in range(count)]

def field(sirens, reference_db=120, attenuation=0.005, threshold=70, size=140, terrain_screen=None, building_screen=None, cancel=None, bounds=None, render_mode="level"):
    """Return transparent PNG data URI and WGS84 bounds for Leaflet."""
    from .acoustics import range_for_level
    effective_levels=[float(s.get('acoustic_reference_db',reference_db))+20*math.log10(float(s.get('acoustic_reference_distance_m',1))) for s in sirens]
    radius = min(12000.0, max(250.0, max(range_for_level(l, threshold-18, attenuation) for l in effective_levels)))
    lat0 = sum(float(s['latitude']) for s in sirens)/len(sirens)
    lon0 = sum(float(s['longitude']) for s in sirens)/len(sirens)
    coslat = max(.15, math.cos(math.radians(lat0)))
    pts = [((float(s['longitude'])-lon0)*math.pi/180*R*coslat,
            (float(s['latitude'])-lat0)*math.pi/180*R, s) for s in sirens]
    xmin = min(x for x,y,s in pts)-radius
    xmax = max(x for x,y,s in pts)+radius
    ymin = min(y for x,y,s in pts)-radius
    ymax = max(y for x,y,s in pts)+radius
    if bounds is not None:
        south,west,north,east=map(float,bounds)
        if south>=north or west>=east:raise ValueError('Ungültiges manuelles Berechnungsgebiet.')
        xmin=(west-lon0)*math.pi/180*R*coslat
        xmax=(east-lon0)*math.pi/180*R*coslat
        ymin=(south-lat0)*math.pi/180*R
        ymax=(north-lat0)*math.pi/180*R
    # Preserve approximate cell resolution for spatially spread sirens.
    width=size
    height=max(40, min(260, round(size*(ymax-ymin)/max(1,xmax-xmin))))
    rows=[]
    for j in range(height):
        if cancel is not None and cancel.is_set():raise InterruptedError()
        y=ymax-(j+.5)*(ymax-ymin)/height
        row=bytearray([0])
        for i in range(width):
            x=xmin+(i+.5)*(xmax-xmin)/width
            energy=0.0
            baseline_energy=0.0
            for siren_index,(sx,sy,s) in enumerate(pts):
                dx=x-sx;dy=y-sy
                d=max(1.0,math.hypot(dx,dy))
                if d>radius*2: continue
                bearing=(math.degrees(math.atan2(dx,dy))+360)%360
                gain=direction_gain(s.get('pattern'),float(s.get('azimuth_deg') or 0),s.get('horn_count'),bearing,s.get('horn_azimuths'))
                from .acoustic_terrain import penalty_at
                terrain_loss=penalty_at(terrain_screen,siren_index,bearing,d)
                base_loss=terrain_loss
                if building_screen is not None:
                    target_lat=lat0+math.degrees(y/R)
                    target_lon=lon0+math.degrees(x/(R*coslat))
                    terrain_loss+=building_screen.penalty(float(s["latitude"]),float(s["longitude"]),target_lat,target_lon,float(s.get("mount_height_m") or 8))
                db=effective_levels[siren_index]-20*math.log10(d)-attenuation*(d-1)+gain-terrain_loss
                if db>threshold-35: energy+=10**((db-threshold)/10)
                if render_mode=="difference":
                    base_db=effective_levels[siren_index]-20*math.log10(d)-attenuation*(d-1)+gain-base_loss
                    if base_db>threshold-80:baseline_energy+=10**((base_db-threshold)/10)
            db=threshold+10*math.log10(energy) if energy else -999
            if db<threshold: rgba=(0,0,0,0)
            elif db<threshold+5: rgba=(32,110,215,110)
            elif db<threshold+10: rgba=(25,175,185,135)
            elif db<threshold+15: rgba=(65,180,90,155)
            elif db<threshold+20: rgba=(245,190,40,170)
            else: rgba=(225,65,45,180)
            if render_mode=="difference":
                if baseline_energy <= 0: rgba=(0,0,0,0)
                else:
                    difference=max(0.0,10*math.log10(baseline_energy/max(energy,1e-30)))
                    if difference<1:rgba=(0,0,0,0)
                    elif difference<5:rgba=(255,218,90,130)
                    elif difference<10:rgba=(249,139,44,165)
                    elif difference<15:rgba=(220,66,48,190)
                    else:rgba=(128,27,98,215)
            row.extend(rgba)
        rows.append(bytes(row))
    raw=b''.join(rows)
    def chunk(tag,data):
        return struct.pack('!I',len(data))+tag+data+struct.pack('!I',zlib.crc32(tag+data)&0xffffffff)
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,6,0,0,0))+
         chunk(b'IDAT',zlib.compress(raw,6))+chunk(b'IEND',b''))
    bounds=[[lat0+math.degrees(ymin/R),lon0+math.degrees(xmin/(R*coslat))],
            [lat0+math.degrees(ymax/R),lon0+math.degrees(xmax/(R*coslat))]]
    return {'image':'data:image/png;base64,'+base64.b64encode(png).decode('ascii'), 'bounds':bounds,
            'width':width,'height':height,'threshold':threshold,'analysis_mode':render_mode}


def level_at(sirens, latitude, longitude, reference_db=120, attenuation=0.005, terrain_screen=None, building_screen=None):
    """Compute approximate combined dB at a map point, plus each siren contribution."""
    contributions=[]
    for index, siren in enumerate(sirens):
        lat1=math.radians(float(siren['latitude'])); lat2=math.radians(float(latitude))
        dlat=lat2-lat1; dlon=math.radians(float(longitude)-float(siren['longitude']))
        h=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        distance=max(1.0, 2*R*math.asin(min(1.0,math.sqrt(h))))
        east=dlon*math.cos((lat1+lat2)/2)*R; north=dlat*R
        bearing=(math.degrees(math.atan2(east,north))+360)%360
        gain=direction_gain(siren.get('pattern'),float(siren.get('azimuth_deg') or 0),siren.get('horn_count'),bearing,siren.get('horn_azimuths'))
        from .acoustic_terrain import penalty_at
        loss=penalty_at(terrain_screen,index,bearing,distance)
        if building_screen is not None:
            loss+=building_screen.penalty(float(siren["latitude"]),float(siren["longitude"]),float(latitude),float(longitude),float(siren.get("mount_height_m") or 8))
        ref=float(siren.get('acoustic_reference_db',reference_db))
        refdist=max(0.01,float(siren.get('acoustic_reference_distance_m',1)))
        db=ref+20*math.log10(refdist)-20*math.log10(distance)-attenuation*(distance-1)+gain-loss
        contributions.append({'name':siren.get('name','Sirene'), 'distance_m':distance,'level_db':db})
    total=10*math.log10(sum(10**(c['level_db']/10) for c in contributions)) if contributions else None
    return {'level_db':total,'contributions':contributions}

"""Coarse radial terrain diffraction screening (single dominant knife edge).
Not ITU-R P.1812, no clutter, no probabilistic service guarantee.
"""
import math
from .dmr import distance_km
from .online_terrain import elevations

def radial_model(repeater, radius_km, cache_folder, rays=36, samples=17, dgm_folder=None, progress=None):
    lat=float(repeater['latitude']); lon=float(repeater['longitude'])
    frequency=float(repeater['frequency_mhz'])
    tx_height=float(repeater['height_m'])
    if radius_km<=0 or frequency<=0 or tx_height<0: raise ValueError('Ungültige Funkparameter')
    points=[]
    for ray in range(rays):
        angle=2*math.pi*ray/rays
        for i in range(samples):
            km=radius_km*i/(samples-1)
            points.append((lat+km*math.cos(angle)/111.2, lon+km*math.sin(angle)/(111.2*max(.15,math.cos(math.radians(lat))))))
    if dgm_folder is None:
        heights=elevations(points,cache_folder)
    else:
        from .terrain import TerrainSampler
        heights=[]
        with TerrainSampler(dgm_folder) as sampler:
            for i,(latitude,longitude) in enumerate(points):
                if progress is not None and (i % 16 == 0 or i == len(points)-1):
                    progress(i+1,len(points))
                heights.append(sampler.sample(latitude,longitude)['height'])
    profiles=[heights[i*samples:(i+1)*samples] for i in range(rays)]
    wavelength=299792458/(frequency*1e6)
    def ray_loss(profile, km, rx_height):
        end_index=max(1,min(samples-1,int(round(km/radius_km*(samples-1)))))
        d=radius_km*end_index/(samples-1)*1000
        if end_index<2:return 0.0
        start=profile[0]+tx_height;end=profile[end_index]+rx_height
        max_v=-100.0
        for j in range(1,end_index):
            t=j/end_index
            bulge=d*d*t*(1-t)/(2*(4/3)*6371000)
            obstacle=profile[j]+bulge-(start+(end-start)*t)
            d1=d*t;d2=d-d1
            v=obstacle*math.sqrt(2*(d1+d2)/(wavelength*d1*d2))
            max_v=max(max_v,v)
        if max_v<=-0.78:return 0.0
        return max(0.0,6.9+20*math.log10(math.sqrt((max_v-.1)**2+1)+max_v-.1))
    def loss_for(lat2,lon2,km,rx_height=1.5):
        east=(lon2-lon)*math.cos(math.radians(lat));north=lat2-lat
        angle=math.atan2(east,north)%(2*math.pi)
        idx=angle*rays/(2*math.pi)
        left=int(idx)%rays;right=(left+1)%rays;f=idx-int(idx)
        return (1-f)*ray_loss(profiles[left],km,rx_height)+f*ray_loss(profiles[right],km,rx_height)
    return loss_for

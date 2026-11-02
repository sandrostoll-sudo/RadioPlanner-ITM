"""DMR terrain LOS / Fresnel screening only; not a propagation loss model."""
import math

def sample_path(lat1,lon1,lat2,lon2,points=41):
    if points<3:raise ValueError('Mindestens drei Stützpunkte erforderlich.')
    return [(lat1+(lat2-lat1)*i/(points-1),lon1+(lon2-lon1)*i/(points-1)) for i in range(points)]

def terrain_clearance(heights,tx_height_m,rx_height_m,distance_km,frequency_mhz,k_factor=4/3):
    if len(heights)<3 or frequency_mhz<=0 or distance_km<0 or tx_height_m<0 or rx_height_m<0 or k_factor<=0:
        raise ValueError('Ungültige Geländedaten oder Antennenparameter.')
    if not all(math.isfinite(float(h)) for h in heights):raise ValueError('Ungültige Höhenwerte.')
    start=float(heights[0])+tx_height_m;end=float(heights[-1])+rx_height_m
    los=[];fres=[]
    for i,h in enumerate(heights):
        t=i/(len(heights)-1)
        bulge=(distance_km*1000)**2*t*(1-t)/(2*k_factor*6371000)
        clearance=start+(end-start)*t-float(h)-bulge
        d1=distance_km*1000*t;d2=distance_km*1000*(1-t)
        radius=math.sqrt((299792458/(frequency_mhz*1e6))*d1*d2/(d1+d2)) if d1+d2 else 0
        los.append(clearance);fres.append(clearance-0.6*radius)
    min_los=min(los[1:-1]);min_fres=min(fres[1:-1])
    status='Sichtlinie durch Gelände blockiert' if min_los<0 else 'Fresnelzone teilweise eingeschränkt' if min_fres<0 else 'Sichtlinie und 60%-Fresnelzone frei (Modell)'
    return dict(distance_km=distance_km,min_los_m=min_los,min_fresnel60_m=min_fres,status=status)

"""Coarse FSPL-only area screening, NOT a terrain-aware coverage model."""
import math
from .dmr import evaluate_link, distance_km
from .dmr_palette import DEFAULT_BANDS, validate_bands, band_for_dbm

def calculate_area(repeater, terminal, radius_km=5.0, required_margin_db=15.0, steps=41, mode="margin", terrain_loss_fn=None, dbm_bands=None, path_loss_fn=None):
    if path_loss_fn is not None and terrain_loss_fn is not None:
        raise ValueError('Vollständiger Pfadverlust und zusätzliche Geländedämpfung dürfen nicht kombiniert werden.')
    if not (0.1 <= radius_km <= 100 and 5 <= steps <= 201):
        raise ValueError('Ungültige Rastergröße oder Radius')
    if mode not in ('margin', 'level', 'relative'):
        raise ValueError('Unbekannte Darstellungsart')
    bands=validate_bands(dbm_bands if dbm_bands is not None else DEFAULT_BANDS) if mode=="level" else None
    power,gain,loss,sensitivity,building_loss=terminal
    lat=float(repeater['latitude']);lon=float(repeater['longitude'])
    dlat=radius_km/111.2
    dlon=radius_km/(111.2*max(0.15,abs(math.cos(math.radians(lat)))))
    cells=[];stats={'good':0,'border':0,'poor':0, 'min_dbm':None, 'max_dbm':None, 'count':0, 'min_terrain_loss_db':None, 'max_terrain_loss_db':None}
    for i in range(steps):
        for j in range(steps):
            a=lat+dlat*(2*(i+0.5)/steps-1)
            b=lon+dlon*(2*(j+0.5)/steps-1)
            dist=distance_km(lat,lon,a,b)
            if dist>radius_km:continue
            res=evaluate_link(dist,repeater.get('tx_frequency_mhz') or repeater['frequency_mhz'],repeater['power_w'],repeater['gain_dbi'],repeater['loss_db'],power,gain,loss,sensitivity,required_margin_db,building_loss,repeater_sensitivity_dbm=repeater['rx_sensitivity_dbm'],uplink_frequency_mhz=repeater.get('rx_frequency_mhz') or repeater['frequency_mhz'])
            terrain_loss=terrain_loss_fn(a,b,dist) if terrain_loss_fn else 0.0
            if path_loss_fn is not None:
                # ITM/P.1812 return TOTAL basic transmission loss, not extra diffraction loss.
                # Keep the independent uplink/downlink frequencies explicit.
                tx_freq = float(repeater.get('tx_frequency_mhz') or repeater['frequency_mhz'])
                rx_freq = float(repeater.get('rx_frequency_mhz') or repeater['frequency_mhz'])
                down_path_loss = float(path_loss_fn(a,b,dist,tx_freq))
                up_path_loss = float(path_loss_fn(a,b,dist,rx_freq))
                if not (math.isfinite(down_path_loss) and math.isfinite(up_path_loss)):
                    raise ValueError('Ausbreitungsmodell lieferte ungültigen Pfadverlust.')
                down_fspl = 32.44+20*math.log10(tx_freq)+20*math.log10(max(dist,0.001))
                up_fspl = 32.44+20*math.log10(rx_freq)+20*math.log10(max(dist,0.001))
                down_extra = down_path_loss-down_fspl
                up_extra = up_path_loss-up_fspl
                res['downlink_dbm'] -= down_extra
                res['uplink_dbm'] -= up_extra
                res['downlink_margin_db'] -= down_extra
                res['uplink_margin_db'] -= up_extra
            stats['min_terrain_loss_db']=terrain_loss if stats['min_terrain_loss_db'] is None else min(stats['min_terrain_loss_db'],terrain_loss)
            stats['max_terrain_loss_db']=terrain_loss if stats['max_terrain_loss_db'] is None else max(stats['max_terrain_loss_db'],terrain_loss)
            reserve=min(res['downlink_margin_db'],res['uplink_margin_db'])-terrain_loss
            status='good' if reserve>=required_margin_db else 'border' if reserve>=required_margin_db-6 else 'poor'
            stats[status]+=1
            level=res['downlink_dbm']-terrain_loss
            stats['count']+=1
            stats['min_dbm']=level if stats['min_dbm'] is None else min(stats['min_dbm'],level)
            stats['max_dbm']=level if stats['max_dbm'] is None else max(stats['max_dbm'],level)
            # Fixed dBm bands permit comparison between different maps.
            if mode=='level':
                band=band_for_dbm(level,bands)
            else:
                band=0 if reserve < required_margin_db-12 else 1 if reserve < required_margin_db-6 else 2 if reserve < required_margin_db else 3 if reserve < required_margin_db+12 else 4
            cells.append(dict(south=a-dlat/steps,north=a+dlat/steps,west=b-dlon/steps,east=b+dlon/steps,status=status,band=band,dbm=round(level,1),terrain_loss_db=round(terrain_loss,1)))
    # Relative level bands visualize real spatial variation without changing
    # absolute dBm values or claiming an additional propagation loss.
    if mode == 'relative' and cells:
        lo,hi=stats['min_dbm'],stats['max_dbm']
        for cell in cells:
            cell['band']=min(4,max(0,int(5*(cell['dbm']-lo)/max(0.01,hi-lo))))
    stats['min_terrain_loss_db']=stats['min_terrain_loss_db'] if cells else 0.0
    stats['max_terrain_loss_db']=stats['max_terrain_loss_db'] if cells else 0.0
    stats['band_counts']=[sum(c['band']==i for c in cells) for i in range(8 if mode=='level' else 5)]
    stats['below_minimum']=sum(c['band'] is None for c in cells) if mode=='level' else 0
    return cells,stats

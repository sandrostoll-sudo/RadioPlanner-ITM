"""Conservative presentation of a free-space DMR Tier II link precheck; not a coverage prediction."""
import math

def distance_km(lat1,lon1,lat2,lon2):
    a1,a2=math.radians(lat1),math.radians(lat2)
    da=math.radians(lat2-lat1);dl=math.radians(lon2-lon1)
    h=math.sin(da/2)**2+math.cos(a1)*math.cos(a2)*math.sin(dl/2)**2
    return 6371.0088*2*math.asin(min(1,math.sqrt(h)))

def evaluate_link(distance_km, frequency_mhz, repeater_power_w, repeater_gain_dbi,
                  repeater_loss_db, terminal_power_w, terminal_gain_dbi, terminal_loss_db,
                  sensitivity_dbm, required_margin_db, building_loss_db=0, repeater_sensitivity_dbm=None, uplink_frequency_mhz=None):
    if distance_km < 0 or frequency_mhz <= 0 or repeater_power_w <= 0 or terminal_power_w <= 0:
        raise ValueError('Ungültige Funkparameter.')
    uplink_frequency_mhz = frequency_mhz if uplink_frequency_mhz is None else uplink_frequency_mhz
    if uplink_frequency_mhz <= 0: raise ValueError("Ungültige RX-Frequenz.")
    fspl=32.44+20*math.log10(frequency_mhz)+20*math.log10(max(distance_km,0.001))
    uplink_fspl=32.44+20*math.log10(uplink_frequency_mhz)+20*math.log10(max(distance_km,0.001))
    down=10*math.log10(repeater_power_w*1000)+repeater_gain_dbi-repeater_loss_db+terminal_gain_dbi-terminal_loss_db-fspl-building_loss_db
    up=10*math.log10(terminal_power_w*1000)+terminal_gain_dbi-terminal_loss_db+repeater_gain_dbi-repeater_loss_db-uplink_fspl-building_loss_db
    dm=down-sensitivity_dbm;um=up-(sensitivity_dbm if repeater_sensitivity_dbm is None else repeater_sensitivity_dbm)
    return dict(downlink_dbm=down,uplink_dbm=up,downlink_margin_db=dm,uplink_margin_db=um,usable=min(dm,um)>=required_margin_db)

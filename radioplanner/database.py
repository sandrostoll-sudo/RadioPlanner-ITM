"""Local SQLite project and transmitter storage."""
import sqlite3
from pathlib import Path

MAX_STATIONS = 100

class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys = ON')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stations (
          id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          name TEXT NOT NULL, latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
          longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
          frequency_mhz REAL NOT NULL CHECK(frequency_mhz > 0),
          power_w REAL NOT NULL CHECK(power_w >= 0),
          antenna_height_m REAL NOT NULL CHECK(antenna_height_m >= 0),
          antenna_name TEXT NOT NULL DEFAULT 'Standard-Rundstrahler 0 dBd',
          cable_loss_db REAL NOT NULL DEFAULT 0 CHECK(cable_loss_db >= 0),
          notes TEXT NOT NULL DEFAULT ''
        );
        ''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS dmr_repeaters (
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
            frequency_mhz REAL NOT NULL, power_w REAL NOT NULL, gain_dbi REAL NOT NULL,
            loss_db REAL NOT NULL, height_m REAL NOT NULL)''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS dmr_devices (
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name TEXT NOT NULL, device_type TEXT NOT NULL CHECK(device_type IN ('handheld','handheld_indoor','vehicle','fixed')),
            latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
            longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180))''')
        # Older beta projects used a CHECK constraint without fixed stations.
        schema = self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='dmr_devices'").fetchone()[0]
        if "'fixed'" not in schema:
            self.conn.execute('ALTER TABLE dmr_devices RENAME TO dmr_devices_legacy')
            self.conn.execute("""CREATE TABLE dmr_devices (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL, device_type TEXT NOT NULL CHECK(device_type IN ('handheld','handheld_indoor','vehicle','fixed')),
                latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
                longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180))""")
            self.conn.execute('INSERT INTO dmr_devices SELECT * FROM dmr_devices_legacy')
            self.conn.execute('DROP TABLE dmr_devices_legacy')
            self.conn.commit()
        # DMR 3.5: independent TX/RX parameters for all device types.
        for table, specs in {
            'dmr_repeaters': {'rx_sensitivity_dbm': 'REAL NOT NULL DEFAULT -117', 'rx_frequency_mhz': 'REAL', 'tx_frequency_mhz': 'REAL'},
            'dmr_devices': {
                'power_w': 'REAL NOT NULL DEFAULT 5',
                'gain_dbi': 'REAL NOT NULL DEFAULT -3',
                'loss_db': 'REAL NOT NULL DEFAULT 1',
                'height_m': 'REAL NOT NULL DEFAULT 1.5',
                'rx_sensitivity_dbm': 'REAL NOT NULL DEFAULT -117',
            },
        }.items():
            for col, spec in specs.items():
                self._add_column(table, col, spec)
        # Migrate legacy single-frequency repeaters without changing their existing value.
        self.conn.execute('UPDATE dmr_repeaters SET rx_frequency_mhz=frequency_mhz WHERE rx_frequency_mhz IS NULL')
        self.conn.execute('UPDATE dmr_repeaters SET tx_frequency_mhz=frequency_mhz WHERE tx_frequency_mhz IS NULL')
        # Additive migrations preserve existing RadioPlanner 0.1 data.
        self._add_column('projects', 'network_frequency_mhz', 'REAL NOT NULL DEFAULT 169.65')
        existing_station_cols = {r['name'] for r in self.conn.execute('PRAGMA table_info(stations)')}
        self._add_column('stations', 'frequency_override_mhz', 'REAL')
        if 'frequency_override_mhz' not in existing_station_cols:
            # Preserve original per-station frequencies when migrating from 0.1.
            self.conn.execute('UPDATE stations SET frequency_override_mhz=frequency_mhz')
        self._add_column('stations', 'terrain_height_m', 'REAL')
        self._add_column('stations', 'system_type', "TEXT NOT NULL DEFAULT 'POCSAG'")
        self._add_column('stations', 'terrain_source', 'TEXT')
        self._add_column('stations', 'terrain_vertical_datum', 'TEXT')
        self._add_column('stations', 'terrain_resolution_m', 'REAL')
        self.conn.execute("""CREATE TABLE IF NOT EXISTS radio_links (
          id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          name TEXT NOT NULL, station_a_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
          station_b_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
          frequency_ghz REAL NOT NULL CHECK(frequency_ghz > 0),
          link_type TEXT NOT NULL CHECK(link_type IN ('Lizenziert','5 GHz')),
          CHECK(station_a_id != station_b_id))""")
        # Additive migration for link engineering parameters (0.3).
        for column, definition in (
            ('bandwidth_mhz', 'REAL NOT NULL DEFAULT 20'),
            ('tx_power_dbm', 'REAL NOT NULL DEFAULT 20'),
            ('rx_sensitivity_dbm', 'REAL NOT NULL DEFAULT -75'),
            ('gain_a_dbi', 'REAL NOT NULL DEFAULT 27'),
            ('gain_b_dbi', 'REAL NOT NULL DEFAULT 27'),
            ('height_a_m', 'REAL NOT NULL DEFAULT 25'),
            ('height_b_m', 'REAL NOT NULL DEFAULT 30'),
            ('loss_a_db', 'REAL NOT NULL DEFAULT 1'),
            ('loss_b_db', 'REAL NOT NULL DEFAULT 1'),
            ('reserve_target_db', 'REAL NOT NULL DEFAULT 20'),
            ('rain_specific_db_km', 'REAL NOT NULL DEFAULT 0'),
            ('gas_specific_db_km', 'REAL NOT NULL DEFAULT 0'),
            ('wet_path_fraction', 'REAL NOT NULL DEFAULT 1'),
            ('itu_rain_rate_mm_h', 'REAL NOT NULL DEFAULT 25'),
            ('itu_polarization', "TEXT NOT NULL DEFAULT 'Horizontal'"),
            ('itu_temperature_c', 'REAL NOT NULL DEFAULT 15'),
            ('itu_pressure_hpa', 'REAL NOT NULL DEFAULT 1013'),
            ('itu_vapor_g_m3', 'REAL NOT NULL DEFAULT 7.5'),
            ('terrain_status', "TEXT NOT NULL DEFAULT 'unknown'"),
            ('terrain_source', 'TEXT'),
            ('terrain_clearance_m', 'REAL'),
            ('terrain_fresnel_clearance_m', 'REAL'),
            ('terrain_fraction', 'REAL'),
            ('antenna_a_model_id', 'TEXT'),
            ('antenna_b_model_id', 'TEXT'),
            ('radio_model_id', 'TEXT'),
        ):
            self._add_column('radio_links', column, definition)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS sirens (
          id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          name TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
          manufacturer TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
          siren_type TEXT NOT NULL, power_w INTEGER,
          pattern TEXT NOT NULL, azimuth_deg REAL NOT NULL DEFAULT 0,
          mounting_height_m REAL NOT NULL DEFAULT 12,
          horn_count INTEGER NOT NULL DEFAULT 1,
          notes TEXT NOT NULL DEFAULT ''
        )""")
        for column, definition in (
            ('terrain_height_m', 'REAL'),
            ('terrain_source', 'TEXT'),
            ('terrain_vertical_datum', 'TEXT'),
            ('terrain_resolution_m', 'REAL'),
        ):
            self._add_column('sirens', column, definition)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS siren_models (
          id INTEGER PRIMARY KEY, manufacturer TEXT NOT NULL, model TEXT NOT NULL,
          power_w REAL, reference_db REAL NOT NULL, reference_distance_m REAL NOT NULL,
          pattern TEXT NOT NULL, horn_count INTEGER NOT NULL DEFAULT 1,
          notes TEXT NOT NULL DEFAULT '',
          CHECK(reference_distance_m > 0), CHECK(power_w IS NULL OR power_w >= 0))""")
        self._add_column('sirens', 'siren_model_id', 'INTEGER')
        self._add_column('sirens', 'horn_azimuths', "TEXT NOT NULL DEFAULT ''")
        self.conn.commit()

    def siren_models(self):
        return self.conn.execute('SELECT * FROM siren_models ORDER BY manufacturer COLLATE NOCASE, model COLLATE NOCASE').fetchall()

    def siren_model(self, model_id):
        return self.conn.execute('SELECT * FROM siren_models WHERE id=?', (model_id,)).fetchone()

    def save_siren_model(self, values, model_id=None):
        cols=('manufacturer','model','power_w','reference_db','reference_distance_m','pattern','horn_count','notes')
        if not values['manufacturer'].strip() or not values['model'].strip():
            raise ValueError('Hersteller und Modellbezeichnung sind erforderlich.')
        if not 0 < values['reference_distance_m'] <= 10000 or not 0 <= values['reference_db'] <= 200:
            raise ValueError('Ungültiger akustischer Referenzwert oder Messabstand.')
        if values['power_w'] is not None and values['power_w'] < 0: raise ValueError('Leistung darf nicht negativ sein.')
        if values['horn_count'] < 1: raise ValueError('Mindestens ein Schalltrichter erforderlich.')
        if model_id is None:
            cur=self.conn.execute('INSERT INTO siren_models ('+','.join(cols)+') VALUES ('+','.join('?' for _ in cols)+')',tuple(values[c] for c in cols))
            model_id=cur.lastrowid
        else:
            cur=self.conn.execute('UPDATE siren_models SET '+','.join(c+'=?' for c in cols)+' WHERE id=?',(*[values[c] for c in cols],model_id))
            if cur.rowcount != 1: raise ValueError('Modell nicht gefunden.')
        self.conn.commit()
        return model_id

    def delete_siren_model(self, model_id):
        if self.conn.execute('SELECT COUNT(*) FROM sirens WHERE siren_model_id=?',(model_id,)).fetchone()[0]:
            raise ValueError('Das Modell ist noch Sirenenstandorten zugeordnet.')
        self.conn.execute('DELETE FROM siren_models WHERE id=?',(model_id,))
        self.conn.commit()

    def sirens(self, project_id):
        return self.conn.execute('SELECT * FROM sirens WHERE project_id=? ORDER BY name COLLATE NOCASE',(project_id,)).fetchall()

    def siren(self, project_id, siren_id):
        return self.conn.execute('SELECT * FROM sirens WHERE project_id=? AND id=?',(project_id,siren_id)).fetchone()

    def save_siren(self, project_id, values, siren_id=None):
        columns=('name','latitude','longitude','manufacturer','model','siren_type','power_w','pattern','azimuth_deg','mounting_height_m','horn_count','notes','terrain_height_m','terrain_source','terrain_vertical_datum','terrain_resolution_m','siren_model_id','horn_azimuths')
        if not values['name'].strip(): raise ValueError('Sirenenname fehlt.')
        if not (-90 <= values['latitude'] <= 90 and -180 <= values['longitude'] <= 180): raise ValueError('Ungültige Koordinaten.')
        if values['power_w'] is not None and values['power_w'] < 0: raise ValueError('Ungültige Leistung.')
        if values['siren_type']=='Elektromechanisch' and values['power_w'] is not None: raise ValueError('Elektromechanische Sirene ohne elektrische Leistungsstufe erfassen.')
        if not 0 <= values['azimuth_deg'] < 360 or values['mounting_height_m'] < 0 or values['horn_count'] < 1: raise ValueError('Ungültige Sirenenparameter.')
        values = dict(values)
        for field in ('terrain_height_m','terrain_source','terrain_vertical_datum','terrain_resolution_m','siren_model_id'):
            values.setdefault(field, None)
        values.setdefault('horn_azimuths', '')
        if values['terrain_height_m'] is not None and not -500 <= values['terrain_height_m'] <= 9000: raise ValueError('Ungültige Geländehöhe.')
        if values['siren_model_id'] is not None and self.siren_model(values['siren_model_id']) is None: raise ValueError('Unbekanntes Sirenenmodell.')
        if siren_id is None:
            sql=f"INSERT INTO sirens(project_id,{','.join(columns)}) VALUES ({','.join('?' for _ in range(len(columns)+1))})"
            cur=self.conn.execute(sql,(project_id,*(values[k] for k in columns)))
            result=cur.lastrowid
        else:
            sql='UPDATE sirens SET '+','.join(f'{k}=?' for k in columns)+' WHERE id=? AND project_id=?'
            cur=self.conn.execute(sql,(*(values[k] for k in columns),siren_id,project_id))
            if cur.rowcount != 1: raise ValueError('Sirene nicht gefunden.')
            result=siren_id
        self.conn.commit()
        return result

    def delete_siren(self, project_id, siren_id):
        self.conn.execute('DELETE FROM sirens WHERE project_id=? AND id=?',(project_id,siren_id))
        self.conn.commit()

    def _add_column(self, table, name, definition):
        existing = {r['name'] for r in self.conn.execute(f'PRAGMA table_info({table})')}
        if name not in existing:
            self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

    def project(self, project_id):
        return self.conn.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()

    def set_project_frequency(self, project_id, frequency):
        if not 0 < frequency <= 1000: raise ValueError('Ungültige Netzfrequenz.')
        self.conn.execute('UPDATE projects SET network_frequency_mhz=? WHERE id=?',(frequency,project_id))
        self.conn.commit()

    def set_station_details(self, station_id, project_id, system_type, terrain_height, override):
        if system_type not in ('POCSAG','DMR'): raise ValueError('Unbekanntes Funksystem.')
        if terrain_height is not None and not -500 <= terrain_height <= 9000: raise ValueError('Ungültige Geländehöhe.')
        if override is not None and not 0 < override <= 1000: raise ValueError('Ungültige Senderfrequenz.')
        self.conn.execute('UPDATE stations SET system_type=?, terrain_height_m=?, frequency_override_mhz=? WHERE id=? AND project_id=?',
                          (system_type,terrain_height,override,station_id,project_id))
        self.conn.commit()

    def set_terrain_result(self, station_id, project_id, elevation, source, vertical_datum, resolution):
        if not -500 <= elevation <= 9000:
            raise ValueError('Geländehöhe außerhalb des erlaubten Bereichs.')
        self.conn.execute('''UPDATE stations SET terrain_height_m=?, terrain_source=?,
          terrain_vertical_datum=?, terrain_resolution_m=? WHERE id=? AND project_id=?''',
          (elevation, source, vertical_datum, resolution, station_id, project_id))
        self.conn.commit()

    def effective_frequency(self, station):
        return station['frequency_override_mhz'] if station['frequency_override_mhz'] is not None else self.project(station['project_id'])['network_frequency_mhz']

    def radio_links(self, project_id):
        return self.conn.execute('SELECT * FROM radio_links WHERE project_id=? ORDER BY name',(project_id,)).fetchall()

    LINK_FIELDS = ('bandwidth_mhz','tx_power_dbm','rx_sensitivity_dbm',
                   'gain_a_dbi','gain_b_dbi','height_a_m','height_b_m',
                   'loss_a_db','loss_b_db','reserve_target_db',
                   'rain_specific_db_km','gas_specific_db_km','wet_path_fraction',
                   'itu_rain_rate_mm_h','itu_polarization','itu_temperature_c','itu_pressure_hpa','itu_vapor_g_m3',
                   'antenna_a_model_id','antenna_b_model_id','radio_model_id')

    def radio_link(self, project_id, link_id):
        return self.conn.execute('SELECT * FROM radio_links WHERE project_id=? AND id=?',
                                 (project_id,link_id)).fetchone()

    def save_radio_link(self, project_id, values, link_id=None):
        name = values['name'].strip()
        a, b = values['station_a_id'], values['station_b_id']
        kind = values['link_type']
        freq = values['frequency_ghz']
        if not name or a == b or kind not in ('Lizenziert','5 GHz'):
            raise ValueError('Ungültige Richtfunkverbindung.')
        ids = {s['id'] for s in self.stations(project_id)}
        if a not in ids or b not in ids:
            raise ValueError('Beide Standorte müssen zum Projekt gehören.')
        if not 0.1 <= freq <= 100:
            raise ValueError('Frequenz muss zwischen 0,1 und 100 GHz liegen.')
        limits = {'bandwidth_mhz':(0.01,2000),'tx_power_dbm':(-100,100),
                  'rx_sensitivity_dbm':(-160,0),'gain_a_dbi':(-30,80),
                  'gain_b_dbi':(-30,80),'height_a_m':(0,10000),
                  'height_b_m':(0,10000),'loss_a_db':(0,100),'loss_b_db':(0,100),'reserve_target_db':(0,80),
                  'rain_specific_db_km':(0,100),'gas_specific_db_km':(0,100),'wet_path_fraction':(0,1),
                  'itu_rain_rate_mm_h':(0,500),'itu_temperature_c':(-50,60),
                  'itu_pressure_hpa':(300,1100),'itu_vapor_g_m3':(0,60)}
        values = dict(values)
        for key,default in (('rain_specific_db_km',0),('gas_specific_db_km',0),('wet_path_fraction',1),
                            ('itu_rain_rate_mm_h',25),('itu_polarization','Horizontal'),
                            ('itu_temperature_c',15),('itu_pressure_hpa',1013),('itu_vapor_g_m3',7.5)):
            values.setdefault(key,default)
        for key,(low,high) in limits.items():
            value=values[key]
            if not low <= value <= high:
                raise ValueError(f'Ungültiger Wert für {key}: {value}')
        values = dict(values)
        for key in ('antenna_a_model_id','antenna_b_model_id','radio_model_id'):
            values.setdefault(key,None)
        if values['itu_polarization'] not in ('Horizontal','Vertikal'):
            raise ValueError('Ungültige Polarisation.')
        cols = ('name','station_a_id','station_b_id','frequency_ghz','link_type') + self.LINK_FIELDS
        if link_id is None:
            self.conn.execute('INSERT INTO radio_links(project_id,'+','.join(cols)+') VALUES ('+
                              ','.join('?' for _ in range(len(cols)+1))+')',
                              (project_id,*(values[k] for k in cols)))
        else:
            cur=self.conn.execute('UPDATE radio_links SET '+','.join(k+'=?' for k in cols)+
                                  ' WHERE id=? AND project_id=?',
                                  (*(values[k] for k in cols),link_id,project_id))
            if cur.rowcount != 1: raise ValueError('Richtfunkverbindung nicht gefunden.')
        if link_id is not None:
            self.conn.execute("UPDATE radio_links SET terrain_status='unknown', terrain_source=NULL, terrain_clearance_m=NULL, terrain_fresnel_clearance_m=NULL, terrain_fraction=NULL WHERE id=? AND project_id=?", (link_id,project_id))
        self.conn.commit()
        return link_id if link_id is not None else self.conn.execute('SELECT last_insert_rowid()').fetchone()[0]

    def save_terrain_assessment(self, project_id, link_id, status, result):
        if status not in ('green','yellow','red','unknown'):
            raise ValueError('Ungültiger Geländestatus')
        self.conn.execute('''UPDATE radio_links SET terrain_status=?, terrain_source=?,
            terrain_clearance_m=?, terrain_fresnel_clearance_m=?, terrain_fraction=?
            WHERE project_id=? AND id=?''',
            (status, result.get('source'), result.get('min_clearance_m'),
             result.get('min_fresnel_60_m'), result.get('fresnel_fraction'), project_id, link_id))
        self.conn.commit()

    def add_radio_link(self, project_id, name, a, b, frequency_ghz, link_type):
        values=dict(name=name,station_a_id=a,station_b_id=b,frequency_ghz=frequency_ghz,
                    link_type=link_type,bandwidth_mhz=20,tx_power_dbm=20,
                    rx_sensitivity_dbm=-75,gain_a_dbi=27,gain_b_dbi=27,
                    height_a_m=25,height_b_m=30,loss_a_db=1,loss_b_db=1,reserve_target_db=20)
        self.save_radio_link(project_id,values)

    def delete_radio_link(self, project_id, link_id):
        self.conn.execute('DELETE FROM radio_links WHERE id=? AND project_id=?',(link_id,project_id))
        self.conn.commit()

    def projects(self):
        return self.conn.execute('''SELECT p.*, COUNT(s.id) AS station_count FROM projects p
          LEFT JOIN stations s ON s.project_id=p.id GROUP BY p.id ORDER BY p.name COLLATE NOCASE''').fetchall()

    def create_project(self, name, description=''):
        if not name.strip(): raise ValueError('Projektname darf nicht leer sein.')
        cur = self.conn.execute('INSERT INTO projects(name,description) VALUES (?,?)', (name.strip(),description))
        self.conn.commit()
        return cur.lastrowid

    def rename_project(self, project_id, name):
        if not name.strip(): raise ValueError('Projektname darf nicht leer sein.')
        self.conn.execute('UPDATE projects SET name=? WHERE id=?', (name.strip(),project_id))
        self.conn.commit()

    def delete_project(self, project_id):
        self.conn.execute('DELETE FROM projects WHERE id=?',(project_id,))
        self.conn.commit()

    def stations(self, project_id):
        return self.conn.execute('SELECT * FROM stations WHERE project_id=? ORDER BY name COLLATE NOCASE',(project_id,)).fetchall()

    def save_station(self, project_id, values, station_id=None):
        columns = ('name','latitude','longitude','frequency_mhz','power_w','antenna_height_m','antenna_name','cable_loss_db','notes')
        if not values['name'].strip(): raise ValueError('Stationsname darf nicht leer sein.')
        if not (-90 <= values['latitude'] <= 90 and -180 <= values['longitude'] <= 180):
            raise ValueError('Ungültige Koordinaten.')
        if values['frequency_mhz'] <= 0 or any(values[k] < 0 for k in ('power_w','antenna_height_m','cable_loss_db')):
            raise ValueError('Frequenz muss positiv sein; Leistung, Höhe und Verlust dürfen nicht negativ sein.')
        if station_id is None:
            count = self.conn.execute('SELECT COUNT(*) FROM stations WHERE project_id=?',(project_id,)).fetchone()[0]
            if count >= MAX_STATIONS: raise ValueError('Maximal 100 Senderstandorte pro Projekt.')
            sql = f"INSERT INTO stations(project_id,{','.join(columns)}) VALUES ({','.join('?' for _ in range(len(columns)+1))})"
            cur = self.conn.execute(sql, (project_id, *(values[k] for k in columns)))
            result = cur.lastrowid
        else:
            sql = 'UPDATE stations SET '+','.join(f'{k}=?' for k in columns)+' WHERE id=? AND project_id=?'
            cur = self.conn.execute(sql, (*(values[k] for k in columns),station_id,project_id))
            if cur.rowcount != 1: raise ValueError('Senderstandort nicht gefunden.')
            result = station_id
            self.conn.execute("UPDATE radio_links SET terrain_status='unknown', terrain_source=NULL, terrain_clearance_m=NULL, terrain_fresnel_clearance_m=NULL, terrain_fraction=NULL WHERE project_id=? AND (station_a_id=? OR station_b_id=?)",(project_id,station_id,station_id))
        self.conn.commit()
        return result

    def delete_station(self, project_id, station_id):
        self.conn.execute('DELETE FROM stations WHERE project_id=? AND id=?',(project_id,station_id))
        self.conn.commit()

    def close(self): self.conn.close()

    def dmr_repeaters(self, project_id):
        return self.conn.execute('SELECT * FROM dmr_repeaters WHERE project_id=? ORDER BY id',(project_id,)).fetchall()

    def dmr_repeater(self, project_id, repeater_id):
        return self.conn.execute('SELECT * FROM dmr_repeaters WHERE project_id=? AND id=?',(project_id,repeater_id)).fetchone()

    def save_dmr_repeater(self, project_id, values, repeater_id=None):
        columns=('name','latitude','longitude','frequency_mhz','power_w','gain_dbi','loss_db','height_m','rx_sensitivity_dbm','rx_frequency_mhz','tx_frequency_mhz')
        values=dict(values)
        values.setdefault('rx_frequency_mhz',values['frequency_mhz'])
        values.setdefault('tx_frequency_mhz',values['frequency_mhz'])
        values['frequency_mhz']=values['tx_frequency_mhz']  # Legacy consumers use TX.
        if repeater_id is None:
            cur=self.conn.execute('INSERT INTO dmr_repeaters (project_id,'+','.join(columns)+') VALUES ('+','.join('?' for _ in range(len(columns)+1))+')',[project_id]+[values[k] for k in columns]);repeater_id=cur.lastrowid
        else:
            self.conn.execute('UPDATE dmr_repeaters SET '+','.join(k+'=?' for k in columns)+' WHERE project_id=? AND id=?',[values[k] for k in columns]+[project_id,repeater_id])
        self.conn.commit();return repeater_id

    def delete_dmr_repeater(self, project_id, repeater_id):
        self.conn.execute('DELETE FROM dmr_repeaters WHERE project_id=? AND id=?',(project_id,repeater_id));self.conn.commit()

    def dmr_devices(self, project_id):
        return self.conn.execute('SELECT * FROM dmr_devices WHERE project_id=? ORDER BY id',(project_id,)).fetchall()

    def save_dmr_device(self, project_id, name, device_type, latitude, longitude, device_id=None, *, power_w=None, gain_dbi=None, loss_db=None, height_m=None, rx_sensitivity_dbm=None):
        if device_type not in ('handheld','handheld_indoor','vehicle','fixed'):
            raise ValueError('Unbekannter DMR-Endgerätetyp')
        defaults = {
            'handheld': (5, -3, 1, 1.5, -117),
            'handheld_indoor': (5, -3, 1, 1.5, -117),
            'vehicle': (25, 2, 1, 2.5, -117),
            'fixed': (25, 4, 2, 10, -117),
        }
        chosen = (power_w, gain_dbi, loss_db, height_m, rx_sensitivity_dbm)
        params = [default if value is None else value for value, default in zip(chosen, defaults[device_type])]
        if not (0 < params[0] <= 1000 and -30 <= params[1] <= 50 and 0 <= params[2] <= 100 and 0 <= params[3] <= 1000 and -160 <= params[4] <= -40):
            raise ValueError('Ungültige technische DMR-Geräteparameter.')
        columns=('name','device_type','latitude','longitude','power_w','gain_dbi','loss_db','height_m','rx_sensitivity_dbm')
        values=(name,device_type,latitude,longitude,*params)
        if device_id is not None:
            self.conn.execute('UPDATE dmr_devices SET '+','.join(k+'=?' for k in columns)+' WHERE id=? AND project_id=?', (*values,device_id,project_id))
            self.conn.commit()
            return device_id
        cur=self.conn.execute('INSERT INTO dmr_devices (project_id,'+','.join(columns)+') VALUES ('+','.join('?' for _ in range(len(columns)+1))+')', (project_id,*values))
        self.conn.commit()
        return cur.lastrowid

    def delete_dmr_device(self, project_id, device_id):
        self.conn.execute('DELETE FROM dmr_devices WHERE project_id=? AND id=?',(project_id,device_id))
        self.conn.commit()

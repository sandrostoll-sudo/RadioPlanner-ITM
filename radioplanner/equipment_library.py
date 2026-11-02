"""User-editable radio/antenna catalog. Example entries are not manufacturer specifications."""
import json
import os
import uuid
from pathlib import Path

EXAMPLE_ITEMS = [
    dict(id='example-antenna-1',category='Antenne',manufacturer='Muster',model='Panel 5 GHz',frequency_min_mhz=5150,frequency_max_mhz=5875,gain_dbi=18,beamwidth_deg=20,polarization='Dual',example=True),
    dict(id='example-antenna-2',category='Antenne',manufacturer='Muster',model='Parabol 18 GHz',frequency_min_mhz=17000,frequency_max_mhz=19000,gain_dbi=35,beamwidth_deg=3,polarization='Horizontal',example=True),
    dict(id='example-radio-1',category='Funkgerät',manufacturer='Muster',model='Funkmodul 5 GHz',frequency_min_mhz=5150,frequency_max_mhz=5875,tx_power_dbm=20,rx_sensitivity_dbm=-75,example=True),
    dict(id='example-radio-2',category='Funkgerät',manufacturer='Muster',model='Funkmodul 18 GHz',frequency_min_mhz=17000,frequency_max_mhz=19000,tx_power_dbm=17,rx_sensitivity_dbm=-72,example=True),
]

class EquipmentLibrary:
    def __init__(self,path=None):
        base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local'/'share')/'RadioPlanner'
        self.path=Path(path) if path else base/'equipment_library.json'
        try:
            self.items=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
            if not isinstance(self.items,list):raise ValueError('Ungültige Bibliothek')
        except (json.JSONDecodeError,UnicodeError) as exc:
            raise ValueError('Gerätebibliothek beschädigt: '+str(exc)) from exc
    def list(self,category):
        return [dict(x) for x in EXAMPLE_ITEMS+self.items if x.get('category')==category]
    def get(self,key):
        return next((dict(x) for x in EXAMPLE_ITEMS+self.items if x.get('id')==key),None)
    def save(self,data,key=None):
        if data.get('category') not in ('Antenne','Funkgerät'):raise ValueError('Ungültige Kategorie')
        if not data.get('manufacturer') or not data.get('model'):raise ValueError('Hersteller und Modell angeben.')
        if not 100<=data['frequency_min_mhz']<=data['frequency_max_mhz']<=100000:
            raise ValueError('Frequenzbereich ist ungültig.')
        if key and any(x['id']==key for x in EXAMPLE_ITEMS):raise ValueError('Muster nicht überschreiben.')
        key=key or str(uuid.uuid4())
        item=dict(data,id=key,example=False)
        self.items=[x for x in self.items if x.get('id')!=key]+[item]
        self._write();return key
    def delete(self,key):
        if any(x['id']==key for x in EXAMPLE_ITEMS):raise ValueError('Muster können nicht gelöscht werden.')
        self.items=[x for x in self.items if x.get('id')!=key];self._write()
    def _write(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.items,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,self.path)

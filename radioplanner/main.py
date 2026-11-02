import sys
import json
import math
import base64
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt, QStandardPaths, QSettings, QThread, Signal
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QSplitter,
 QGroupBox,QListWidget,QListWidgetItem,QPushButton,QLabel,QLineEdit,QDoubleSpinBox,
 QComboBox,QFormLayout,QScrollArea,QToolBox,QMessageBox,QInputDialog,QPlainTextEdit,QCheckBox,QDialog,QDialogButtonBox,QFileDialog,QSpinBox,QProgressDialog,QColorDialog,QTabWidget,QTextEdit)
from PySide6.QtGui import QKeySequence, QShortcut, QColor, QPainter, QFont
from .database import Database
from .dmr_palette import DEFAULT_BANDS, validate_bands, load_bands
from .antennas import ANTENNAS
from .mapview import MapView
from .terrain import sample_folder, TerrainError
from .profile import analyze
from .online_terrain import sample_online
from .profile_view import ProfilePlot
from .link_budget import calculate as calculate_link_budget, status_for_margin
from .overall_assessment import assess as assess_overall
from .equipment_library import EquipmentLibrary, EXAMPLE_ITEMS

class CalculationWorker(QThread):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        import threading
        self.cancel_event=threading.Event()
        self.task=task

    def run(self):
        try:
            result=self.task(self.progress.emit,self.cancel_event)
            if not self.cancel_event.is_set():self.completed.emit(result)
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.cancel_event.is_set():self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('RadioPlanner 4.0.15 – DMR-Frequenzpaare (Beta)')
        self.resize(1450,850)
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        self.db = Database(data_dir / 'radioplanner.sqlite3')
        self.project_id = None
        self.project_files = {}
        self.last_dmr_result = None
        self.station_id = None
        self.pending_coords = None
        self.map_mode = 'idle'
        self._dmr_map_pick = None
        self._dmr_escape = QShortcut(QKeySequence('Esc'), self)
        self._dmr_escape.setContext(Qt.ApplicationShortcut)
        self._dmr_escape.activated.connect(self.cancel_dmr_map_pick)
        self.move_siren_id = None
        self.analysis_polygon = None
        self.simulated_siren = None
        self.probe_data = None
        self.probe_mode = False
        self.terrain_folder = str(data_dir / "terrain_library")
        Path(self.terrain_folder).mkdir(parents=True, exist_ok=True)
        self.terrain_result = None
        self.online_cache = data_dir / 'terrain_cache'
        self.diagnostic_messages = []
        self.report_profile_folder = data_dir / 'report_profiles'
        from .building_cache import shared_cache_dir, migrate_legacy_cache
        self.building_cache = shared_cache_dir()
        migrate_legacy_cache(self.online_cache, self.building_cache)
        self.settings = QSettings("RadioPlanner", "RadioPlanner")
        stored = self.settings.value('terrain/library_path', '')
        if stored and Path(str(stored)).is_dir(): self.terrain_folder = str(stored)
        self.map_fullscreen = False
        root = QSplitter(Qt.Horizontal)
        self.main_splitter = root
        root.setChildrenCollapsible(True)
        self.setCentralWidget(root)
        left = QWidget(); ll = QVBoxLayout(left)
        self.left_panel = left
        self.left_scroll = QScrollArea()
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setMinimumWidth(0)
        ll.addWidget(QLabel('PROJEKTE'))
        self.projects = QListWidget(); ll.addWidget(self.projects)
        pbuttons = QHBoxLayout()
        for title,handler in [('+ Projekt',self.add_project),('Umbenennen',self.rename_project),('Löschen',self.delete_project)]:
            b=QPushButton(title);b.clicked.connect(handler);pbuttons.addWidget(b)
        ll.addLayout(pbuttons)
        net=QHBoxLayout()
        net.addWidget(QLabel('Netzfrequenz MHz:'))
        self.network_freq=self.spin(1,1000,4,169.65)
        net.addWidget(self.network_freq)
        change=QPushButton('Übernehmen');change.clicked.connect(self.save_network_frequency);net.addWidget(change)
        ll.addLayout(net)
        ll.addWidget(QLabel('SENDERSTANDORTE (max. 100 je Projekt)'))
        self.stations = QListWidget();ll.addWidget(self.stations)
        sb = QHBoxLayout()
        for title,handler in [('+ Sender',self.new_station),('Löschen',self.delete_station)]:
            b=QPushButton(title);b.clicked.connect(handler);sb.addWidget(b)
        ll.addLayout(sb)
        ll.addWidget(QLabel('RICHTFUNKVERBINDUNGEN (Planungsdaten)'))
        self.links=QListWidget();ll.addWidget(self.links)
        lb=QHBoxLayout()
        addlink=QPushButton('+ Verbindung');addlink.clicked.connect(self.add_link);lb.addWidget(addlink)
        editlink=QPushButton('Eigenschaften');editlink.clicked.connect(self.edit_link);lb.addWidget(editlink)
        dellink=QPushButton('Löschen');dellink.clicked.connect(self.delete_link);lb.addWidget(dellink)
        ll.addLayout(lb)
        self.links.itemDoubleClicked.connect(lambda item: self.edit_link())
        profile_button=QPushButton('Verbindung analysieren (Gelände / Fresnel)')
        profile_button.clicked.connect(self.open_profile_dialog)
        ll.addWidget(profile_button)
        ll.addWidget(QLabel('SIRENEN (gemeinsames Projekt)'))
        self.sirens=QListWidget();ll.addWidget(self.sirens)
        siren_buttons=QHBoxLayout()
        for title,handler in [('+ Sirene',self.add_siren),('Auf Karte setzen',self.place_siren),('Verschieben',self.move_siren),('Eigenschaften',self.edit_siren),('Löschen',self.delete_siren)]:
            button=QPushButton(title);button.clicked.connect(handler);siren_buttons.addWidget(button)
        ll.addLayout(siren_buttons)
        catalog_button=QPushButton('Sirenendatenbank verwalten');catalog_button.clicked.connect(self.manage_siren_models);ll.addWidget(catalog_button)
        self.sirens.itemDoubleClicked.connect(lambda item:self.edit_siren())
        # Only one entry in the sidebar. All acoustic tools live in the
        # existing "Versorgungslücken und Flächenanalyse" popup.
        acoustic_button=QPushButton('Versorgungslücken und Flächenanalyse ...')
        acoustic_button.clicked.connect(self.show_coverage_gaps)
        ll.addWidget(acoustic_button)
        self.show_buildings_checkbox=None
        self.comparison_mode=None
        self._coverage_comparison_ready=False
        self.last_area_report=None
        self._make_left_sections(ll)
        self.left_scroll.setWidget(left)
        root.addWidget(self.left_scroll)
        self.map = MapView();root.addWidget(self.map)
        right=QWidget();rl=QVBoxLayout(right)
        rl.addWidget(QLabel('SENDEREIGENSCHAFTEN'))
        form=QFormLayout()
        self.name=QLineEdit();form.addRow('Name',self.name)
        self.lat=self.spin(-90,90,6,49.32);form.addRow('Breite (°)',self.lat)
        self.lon=self.spin(-180,180,6,7.0);form.addRow('Länge (°)',self.lon)
        self.system=QComboBox();self.system.addItems(['POCSAG','DMR']);form.addRow('Funksystem',self.system)
        self.override=QCheckBox('Abweichende Senderfrequenz');form.addRow('',self.override)
        self.freq=self.spin(1,1000,4,169.65);form.addRow('Senderfrequenz (MHz)',self.freq)
        self.override.toggled.connect(self.update_frequency_control)
        self.power=self.spin(0,100000,2,10);form.addRow('Sendeleistung (W)',self.power)
        self.height=self.spin(0,10000,1,20);form.addRow('Antennenhöhe über Grund (m)',self.height)
        self.terrain_known=QCheckBox('Geländehöhe verwenden / manuell ändern');form.addRow('',self.terrain_known)
        choose_terrain=QPushButton('DGM-Ordner wählen (GeoTIFF)');choose_terrain.clicked.connect(self.choose_terrain_folder)
        form.addRow('Geländedaten',choose_terrain)
        self.terrain_folder_label=QLabel(self.terrain_folder);self.terrain_folder_label.setWordWrap(True)
        form.addRow('',self.terrain_folder_label)
        query_terrain=QPushButton('Geländehöhe für Koordinaten abfragen');query_terrain.clicked.connect(self.query_terrain)
        form.addRow('',query_terrain)
        online_button=QPushButton('Geländehöhe online abfragen (ca. 90 m)')
        online_button.clicked.connect(self.query_online_terrain)
        form.addRow('',online_button)
        self.terrain_source_label=QLabel('Quelle: manuell / unbekannt');self.terrain_source_label.setWordWrap(True)
        form.addRow('',self.terrain_source_label)
        self.terrain=self.spin(-500,9000,1,0);form.addRow('Geländehöhe (m ü. NHN)',self.terrain)
        self.absolute=QLabel('Noch nicht bestimmt');form.addRow('Antennenhöhe (m ü. NHN)',self.absolute)
        self.terrain_known.toggled.connect(self.update_height)
        self.terrain.valueChanged.connect(self.update_height)
        self.height.valueChanged.connect(self.update_height)
        self.antenna=QComboBox();self.antenna.addItems([a['name'] for a in ANTENNAS]);form.addRow('Antenne',self.antenna)
        self.loss=self.spin(0,100,2,1);form.addRow('Kabelverlust (dB)',self.loss)
        self.notes=QPlainTextEdit();self.notes.setMaximumHeight(100);form.addRow('Notizen',self.notes)
        rl.addLayout(form)
        self.save=QPushButton('Sender speichern');self.save.clicked.connect(self.save_station);rl.addWidget(self.save)
        rl.addWidget(QLabel('Hinweis: Funkfeldberechnung und Hersteller-Pattern-Import folgen in späteren Versionen.'))
        rl.addStretch()
        self.right_panel = right
        self.right_scroll = QScrollArea()
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setMinimumWidth(0)
        self.right_scroll.setWidget(right)
        root.addWidget(self.right_scroll)
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setStretchFactor(2, 0)
        root.setSizes([290,850,310])
        self._setup_file_menu()
        self._setup_view_controls()
        self._setup_settings_menu()
        self.map.apply_symbol_settings(self._symbol_settings())
        self.projects.currentItemChanged.connect(self.project_changed)
        self.stations.currentItemChanged.connect(self.station_changed)
        self.map.bridge.coordinates.connect(self.map_clicked)
        self.map.bridge.polygon_ready.connect(self.polygon_received)
        self.map.bridge.probe_requested.connect(self.probe_clicked)
        self.map.bridge.station_selected.connect(self.select_station)
        self.map.bridge.dmr_selected.connect(self.select_dmr_marker)
        self.right_scroll.hide()
        self.main_splitter.setSizes([290, 1160, 0])
        self.statusBar().showMessage('Online-Karte: OpenStreetMap / Leaflet. Daten lokal in SQLite.')
        self.reload_projects()
        self.update_height()

    def _make_left_sections(self, layout):
        """Organize existing controls without recreating or disconnecting them."""
        items = []
        while layout.count():
            items.append(layout.takeAt(0))
        sections = [
            ("Projekte", items[0:4]),
            ("Senderstandorte", items[4:7]),
            ("Richtfunk", items[7:11]),
            ("Sirenen", items[11:14]),
            ("Analysen und Karte", items[14:]),
        ]
        self.section_box = QToolBox()
        for title, group in sections:
            if title == 'Senderstandorte':
                for item in group:
                    widget = item.widget()
                    if widget: widget.hide()
                continue
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(5, 5, 5, 5)
            for item in group:
                widget = item.widget()
                child_layout = item.layout()
                if widget is not None:
                    page_layout.addWidget(widget)
                elif child_layout is not None:
                    page_layout.addLayout(child_layout)
            page_layout.addStretch(1)
            self.section_box.addItem(page, title)
        layout.addWidget(self.section_box)
        self.section_box.setCurrentIndex(int(self.settings.value('section', 0)))
        self.section_box.currentChanged.connect(lambda i: self.settings.setValue('section', i))

    SYMBOL_CHOICES = {
        'sender': [('Funkturm','tower'),('Antenne','antenna'),('Funkgerät','radio'),('Standortmarker','pin')],
        'siren': [('Sirene','megaphone'),('Lautsprecher','speaker'),('Alarmglocke','bell'),('Standortmarker','pin')],
        'link': [('Richtfunkspiegel','dish'),('Funkturm','tower'),('Funknetz','network'),('Antenne','antenna')],
    }

    def _symbol_settings(self):
        defaults={'sender':('#16803c','tower'),'siren':('#ea580c','megaphone'),'link':('#2563eb','dish')}
        result={}
        for key,(color,shape) in defaults.items():
            result[key]={'color':str(self.settings.value('symbols/'+key+'/color',color)),
                         'shape':str(self.settings.value('symbols/'+key+'/shape',shape)),
                         'size':int(self.settings.value('symbols/'+key+'/size',30)),
                         'custom':str(self.settings.value('symbols/'+key+'/custom',''))}
        return result

    def _setup_file_menu(self):
        menu=self.menuBar().addMenu('Datei')
        menu.addAction('Neues Projekt ...').triggered.connect(self.add_project)
        menu.addAction('Projekt öffnen ...').triggered.connect(self.open_project_file)
        save=menu.addAction('Projekt speichern')
        save.setShortcut(QKeySequence.Save)
        save.triggered.connect(self.save_current_project)
        menu.addAction('Projekt speichern unter ...').triggered.connect(self.save_project_as)
        menu.addSeparator()
        menu.addAction('DMR-Berechnung im Projekt ablegen ...').triggered.connect(self.archive_dmr_result)
        menu.addAction('Gespeicherte DMR-Berechnung öffnen ...').triggered.connect(self.open_archived_dmr_result)
        menu.addAction('DMR-Karte als PNG/JPG exportieren ...').triggered.connect(self.export_dmr_map)
        reports=menu.addMenu('Berichte')
        reports.addAction('Ausgewählte Richtfunkstrecke als PDF ...').triggered.connect(self.export_selected_link_report)
        reports.addAction('Richtfunk-Gesamtbericht als PDF ...').triggered.connect(self.export_all_link_reports)
        menu.addSeparator()
        menu.addAction('Beenden').triggered.connect(self.close)

    def archive_dmr_result(self):
        if self.project_id is None:
            return self.alert('Bitte zuerst ein Projekt auswählen.')
        if not self.last_dmr_result:
            return self.alert('Bitte zuerst eine DMR-Flächenberechnung durchführen.')
        from .dmr_result_archive import load, save, MAX_RESULTS
        try:
            results=load(self.report_profile_folder,self.project_id)
            if len(results)>=MAX_RESULTS:
                return self.alert('Maximal 30 DMR-Berechnungen je Projekt. Bitte alte Ergebnisse entfernen.')
            default=f"DMR {datetime.now().strftime('%Y-%m-%d %H:%M')} – {self.last_dmr_result['metadata'].get('scenario','')}"
            name,ok=QInputDialog.getText(self,'DMR-Berechnung ablegen','Bezeichnung:',text=default)
            if not ok or not name.strip():return
            entry=dict(self.last_dmr_result)
            entry['name']=name.strip()[:160]
            results.append(entry)
            save(self.report_profile_folder,self.project_id,results)
            self.statusBar().showMessage('DMR-Berechnung im Projektarchiv gespeichert. Projektdatei bei Bedarf erneut speichern.',8000)
        except Exception as exc:
            self.alert('DMR-Berechnung konnte nicht gespeichert werden: '+str(exc))

    def open_archived_dmr_result(self):
        if self.project_id is None:return self.alert('Bitte zuerst ein Projekt auswählen.')
        from .dmr_result_archive import load
        try:
            results=load(self.report_profile_folder,self.project_id)
            if not results:return self.alert('Keine gespeicherten DMR-Berechnungen in diesem Projekt.')
            labels=[f"{i+1}. {entry['name']}" for i,entry in enumerate(results)]
            choice,ok=QInputDialog.getItem(self,'DMR-Ergebnis öffnen','Gespeicherte Berechnung:',labels,0,False)
            if not ok:return
            entry=results[labels.index(choice)]
            meta=entry['metadata']
            self.map.set_dmr_area_coverage(entry['cells'],coverage_only=meta.get('coverage_only',False),palette_bands=meta.get('palette_bands'))
            self.last_dmr_result=entry
            QMessageBox.information(self,'DMR-Berechnung wiederhergestellt',meta.get('summary','Gespeicherte DMR-Karte wiederhergestellt.'))
        except Exception as exc:
            self.alert('DMR-Ergebnis konnte nicht geöffnet werden: '+str(exc))

    def export_dmr_map(self):
        if not self.map._dmr_area:
            return self.alert('Keine DMR-Versorgungskarte vorhanden.')
        filename,selected=QFileDialog.getSaveFileName(self,'DMR-Karte exportieren','DMR_Versorgungskarte.png',
            'PNG-Bild (*.png);;JPEG-Bild (*.jpg *.jpeg)')
        if not filename:return
        is_jpg='JPEG' in selected or filename.lower().endswith(('.jpg','.jpeg'))
        if not filename.lower().endswith(('.png','.jpg','.jpeg')):filename += '.jpg' if is_jpg else '.png'
        try:
            # WYSIWYG: includes the visible OSM background, DMR overlay and markers.
            pix=self.map.grab()
            if pix.isNull():raise ValueError('Kartenansicht konnte nicht erfasst werden.')
            from PySide6.QtGui import QPixmap
            out=QPixmap(pix.width(),pix.height()+190)
            out.fill(Qt.white)
            painter=QPainter(out)
            painter.drawPixmap(0,190,pix)
            painter.setPen(QColor('#1d2939'))
            painter.setFont(QFont('Arial',13,QFont.Bold))
            painter.drawText(18,30,'RadioPlanner – DMR-Flächenversorgung (Vorabschätzung)')
            painter.setFont(QFont('Arial',9))
            meta=(self.last_dmr_result or {}).get('metadata',{})
            painter.drawText(18,55,('Empfangsart: '+str(meta.get('scenario','nicht angegeben')))[:160])
            painter.drawText(18,76,('Modell: '+str(meta.get('terrain','nicht angegeben'))+' | Radius: '+str(meta.get('radius_km','?'))+' km')[:180])
            painter.drawText(18,98,'Hinweis: Nicht validierte Funkversorgungs-Vorabschätzung; Kartenstand entspricht der aktuellen Ansicht.')
            bands=meta.get('palette_bands') or []
            if bands:
                painter.setFont(QFont('Arial',9,QFont.Bold))
                painter.drawText(18,125,'dBm-Farbklassen (stärkste bis schwächste):')
                painter.setFont(QFont('Arial',8))
                for i,band in enumerate(bands[:8]):
                    x=18+(i%4)*max(170,(pix.width()-30)//4)
                    y=145+(i//4)*21
                    painter.fillRect(x,y-10,13,13,QColor(band['color']))
                    painter.drawText(x+19,y+1,f"{band['label']}: ab {band['min']:g} dBm")
            painter.end()
            if not out.save(filename,'JPG' if is_jpg else 'PNG',95 if is_jpg else -1):
                raise OSError('Bild konnte nicht geschrieben werden.')
            self.statusBar().showMessage('DMR-Karte exportiert: '+filename,8000)
        except Exception as exc:
            self.alert('Kartenexport fehlgeschlagen: '+str(exc))

    def open_project_file(self):
        from .project_io import import_project
        filename,_=QFileDialog.getOpenFileName(self,'Projekt öffnen','','RadioPlanner-Projekte (*.rplan)')
        if not filename:return
        try:
            new_id=import_project(self.db,filename,self.report_profile_folder)
            self.project_files[new_id]=filename
            self.reload_projects(new_id)
            self.statusBar().showMessage('Projektdatei geöffnet und in die lokale Datenbank importiert.',6000)
        except Exception as exc:
            self.alert('Projekt konnte nicht geöffnet werden: '+str(exc))

    def choose_project(self):
        entries=[(self.projects.item(i).text(),i) for i in range(self.projects.count())]
        if not entries:return self.alert('Noch keine Projekte vorhanden.')
        name,ok=QInputDialog.getItem(self,'Projekt auswählen','Projekt:',[e[0] for e in entries],0,False)
        if ok:self.projects.setCurrentRow(next(i for title,i in entries if title==name))

    def save_current_project(self):
        if self.project_id is None:return self.alert('Bitte zuerst ein Projekt auswählen.')
        filename=self.project_files.get(self.project_id)
        if not filename:return self.save_project_as()
        self._write_project(filename)

    def save_project_as(self):
        if self.project_id is None:return self.alert('Bitte zuerst ein Projekt auswählen.')
        project=self.db.project(self.project_id)
        suggested=str(project['name']).replace('/','_').replace('\\','_')+'.rplan'
        filename,_=QFileDialog.getSaveFileName(self,'Projekt speichern unter',suggested,'RadioPlanner-Projekte (*.rplan)')
        if not filename:return
        if not filename.lower().endswith('.rplan'):filename+='.rplan'
        if Path(filename).exists():
            answer=QMessageBox.question(self,'Datei überschreiben',
                'Die Projektdatei existiert bereits. Wirklich überschreiben?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer!=QMessageBox.Yes:return
        self._write_project(filename)

    def _write_project(self,filename):
        from .project_io import export_project
        try:
            export_project(self.db,self.project_id,filename,self.report_profile_folder)
            self.project_files[self.project_id]=filename
            self.statusBar().showMessage('Projektdatei gespeichert: '+filename,6000)
        except Exception as exc:
            self.alert('Projekt konnte nicht gespeichert werden: '+str(exc))

    def _setup_settings_menu(self):
        menu=self.menuBar().addMenu('Einstellungen')
        radio=menu.addMenu('Funkversorgung')
        radio.addAction('dBm-Farbpalette (8 Stufen) ...').triggered.connect(self.open_dbm_palette_settings)
        menu.addAction('Kartensymbole ...').triggered.connect(self.open_symbol_settings)
        menu.addAction('Geräte und Antennen ...').triggered.connect(self.open_equipment_settings)
        menu.addAction('Geländedaten verwalten ...').triggered.connect(self.open_terrain_library)
        dmr=self.menuBar().addMenu('DMR Tier II')
        dmr.addAction('Repeater / Relaisstationen ...').triggered.connect(self.open_dmr_repeaters)
        dmr.addAction('Handfunkgeräte ...').triggered.connect(lambda:self.open_dmr_devices('handheld'))
        dmr.addAction('Fahrzeugfunkgeräte ...').triggered.connect(lambda:self.open_dmr_devices('vehicle'))
        dmr.addAction('Feststationen ...').triggered.connect(lambda:self.open_dmr_devices('fixed'))
        dmr.addAction('Einzelverbindung berechnen ...').triggered.connect(self.open_dmr_coverage)
        dmr.addAction('Flächenversorgung (Vorabschätzung) ...').triggered.connect(self.open_dmr_area)

    def refresh_dmr_markers(self):
        if self.project_id is None:
            self.map.set_dmr_markers([])
            return
        markers=[dict(r,kind='repeater') for r in map(dict,self.db.dmr_repeaters(self.project_id))]
        markers.extend(dict(r,kind=r['device_type']) for r in map(dict,self.db.dmr_devices(self.project_id)))
        self.map.set_dmr_markers(markers)

    def select_dmr_marker(self, kind, marker_id):
        if self.map_mode=='dmr_pick':
            # Clicking an existing marker during placement should choose its coordinates.
            rows=self.db.dmr_repeaters(self.project_id) if kind=='repeater' else self.db.dmr_devices(self.project_id)
            record=next((r for r in rows if r['id']==marker_id),None)
            if record:self.map_clicked(record['latitude'],record['longitude'])
            return
        if kind=='repeater':self.open_dmr_repeaters(marker_id)
        else:self.open_dmr_devices('vehicle' if kind=='vehicle' else 'fixed' if kind=='fixed' else 'handheld',marker_id)

    def _dmr_dialog_base(self, title):
        if self.project_id is None:
            self.alert('Bitte zuerst ein Projekt auswählen.')
            return None,None
        dlg=QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowMaximizeButtonHint)
        dlg.resize(590,530)
        layout=QVBoxLayout(dlg)
        return dlg,layout

    def _dmr_pick_position(self,dlg,lat,lon,description,on_position=None):
        if self.map_mode=='dmr_pick':self.cancel_dmr_map_pick()
        self._dmr_map_pick=(dlg,lat,lon,on_position)
        self.map_mode='dmr_pick'
        self.map.set_hint(description+' auf der Karte anklicken (Esc zum Abbrechen)')
        dlg.hide()
        self.map.raise_()
        self.map.setFocus()
        self.statusBar().showMessage(description+': auf die Karte klicken.',10000)

    def open_dmr_repeaters(self,selected_id=None):
        dlg,layout=self._dmr_dialog_base('DMR Tier II – Repeater / Relaisstationen')
        if dlg is None:return
        saved=QComboBox();layout.addWidget(saved)
        form=QFormLayout();layout.addLayout(form)
        name=QLineEdit('DMR-Repeater');form.addRow('Name',name)
        lat=self.spin(-90,90,6,49.3);lon=self.spin(-180,180,6,7.0)
        form.addRow('Breitengrad',lat);form.addRow('Längengrad',lon)
        freq=self.spin(30,1000,5,439);form.addRow('Repeater TX / Downlink (MHz)',freq)
        rx_freq=self.spin(30,1000,5,439);form.addRow('Repeater RX / Uplink (MHz)',rx_freq)
        tx=self.spin(.01,1000,2,25);form.addRow('Sendeleistung W',tx)
        gain=self.spin(-10,30,2,5);form.addRow('Antennengewinn dBi',gain)
        loss=self.spin(0,30,2,2);form.addRow('Kabel-/Duplexerverlust dB',loss)
        height=self.spin(0,500,1,25);form.addRow('Antennenhöhe über Grund m',height)
        rx=self.spin(-160,-40,1,-117);form.addRow('Empfängerempfindlichkeit dBm',rx)
        fields=(name,lat,lon,freq,tx,gain,loss,height,rx)
        keys=('name','latitude','longitude','frequency_mhz','power_w','gain_dbi','loss_db','height_m','rx_sensitivity_dbm')
        def refresh():
            saved.blockSignals(True);saved.clear();saved.addItem('Neuer Repeater',None)
            for r in self.db.dmr_repeaters(self.project_id):saved.addItem(r['name'],r['id'])
            saved.blockSignals(False)
        def load():
            rid=saved.currentData()
            if rid is None:return
            r=self.db.dmr_repeater(self.project_id,rid)
            if r:
                for w,k in zip(fields,keys):w.setText(r[k]) if isinstance(w,QLineEdit) else w.setValue(r[k])
                rx_freq.setValue(r['rx_frequency_mhz'] if r['rx_frequency_mhz'] is not None else r['frequency_mhz'])
        def save():
            values={k:(w.text().strip() if isinstance(w,QLineEdit) else w.value()) for k,w in zip(keys,fields)}
            if not values['name']:return self.alert('Bitte Repeaternamen eingeben.')
            values['tx_frequency_mhz']=freq.value()
            values['rx_frequency_mhz']=rx_freq.value()
            rid=self.db.save_dmr_repeater(self.project_id,values,saved.currentData())
            refresh();saved.setCurrentIndex(saved.findData(rid));self.refresh_dmr_markers()
        def delete():
            if saved.currentData() is None:return
            self.db.delete_dmr_repeater(self.project_id,saved.currentData());refresh();self.refresh_dmr_markers()
        saved.currentIndexChanged.connect(load)
        row=QHBoxLayout();layout.addLayout(row)
        for label,fn in [('Auf Karte platzieren',lambda:self._dmr_pick_position(dlg,lat,lon,'Repeater')),
                         ('Speichern',save),('Löschen',delete),('Schließen',dlg.accept)]:
            button=QPushButton(label);button.clicked.connect(fn);row.addWidget(button)
        dlg.finished.connect(lambda _ : self._dmr_dialog_closed(dlg))
        refresh()
        if selected_id is not None:saved.setCurrentIndex(saved.findData(selected_id))
        dlg.show()

    def open_dmr_devices(self,category='handheld',selected_id=None):
        vehicle=category=='vehicle'
        fixed=category=='fixed'
        dlg,layout=self._dmr_dialog_base('DMR Tier II – '+('Feststationen' if fixed else 'Fahrzeugfunkgeräte' if vehicle else 'Handfunkgeräte'))
        if dlg is None:return
        saved=QComboBox();layout.addWidget(saved)
        form=QFormLayout();layout.addLayout(form)
        name=QLineEdit('DMR-'+('Feststation' if fixed else 'Fahrzeugfunkgerät' if vehicle else 'Handfunkgerät'))
        form.addRow('Gerätename',name)
        if not vehicle and not fixed:
            indoor=QCheckBox('Betrieb im Gebäude (für spätere Versorgungsberechnung)')
            form.addRow('Szenario',indoor)
        else:indoor=None
        lat=self.spin(-90,90,6,49.3);lon=self.spin(-180,180,6,7.0)
        form.addRow('Breitengrad',lat);form.addRow('Längengrad',lon)
        defaults=(25,4,2,10) if fixed else (25,2,1,2.5) if vehicle else (5,-3,1,1.5)
        tx=self.spin(.01,1000,2,defaults[0]);form.addRow('Sendeleistung W',tx)
        gain=self.spin(-30,50,2,defaults[1]);form.addRow('Antennengewinn dBi',gain)
        loss=self.spin(0,100,2,defaults[2]);form.addRow('Kabel-/Steckerverlust dB',loss)
        height=self.spin(0,1000,2,defaults[3]);form.addRow('Antennenhöhe über Grund m',height)
        rx=self.spin(-160,-40,1,-117);form.addRow('Empfängerempfindlichkeit dBm',rx)
        info=QLabel('Position über die Karte wählen, anschließend speichern. Das Gerät wird mit eigenem Symbol angezeigt.')
        info.setWordWrap(True);layout.addWidget(info)
        def relevant(r):return r['device_type']=='fixed' if fixed else r['device_type']=='vehicle' if vehicle else r['device_type'] in ('handheld','handheld_indoor')
        def refresh():
            saved.blockSignals(True);saved.clear();saved.addItem('Neues Gerät',None)
            for r in self.db.dmr_devices(self.project_id):
                if relevant(r):saved.addItem(r['name'],r['id'])
            saved.blockSignals(False)
        def load():
            rid=saved.currentData()
            if rid is None:return
            r=next((r for r in self.db.dmr_devices(self.project_id) if r['id']==rid),None)
            if r:
                name.setText(r['name']);lat.setValue(r['latitude']);lon.setValue(r['longitude'])
                for widget,key in ((tx,'power_w'),(gain,'gain_dbi'),(loss,'loss_db'),(height,'height_m'),(rx,'rx_sensitivity_dbm')):widget.setValue(r[key])
                if indoor:indoor.setChecked(r['device_type']=='handheld_indoor')
        def save():
            if not name.text().strip():return self.alert('Bitte Gerätenamen eingeben.')
            kind='fixed' if fixed else 'vehicle' if vehicle else ('handheld_indoor' if indoor.isChecked() else 'handheld')
            rid=self.db.save_dmr_device(self.project_id,name.text().strip(),kind,lat.value(),lon.value(),saved.currentData(),power_w=tx.value(),gain_dbi=gain.value(),loss_db=loss.value(),height_m=height.value(),rx_sensitivity_dbm=rx.value())
            refresh();saved.setCurrentIndex(saved.findData(rid));self.refresh_dmr_markers()
            self.statusBar().showMessage('DMR-Gerät gespeichert. Projektdatei ebenfalls speichern.',6000)
        def delete():
            if saved.currentData() is None:return
            self.db.delete_dmr_device(self.project_id,saved.currentData());refresh();self.refresh_dmr_markers()
        saved.currentIndexChanged.connect(load)
        row=QHBoxLayout();layout.addLayout(row)
        for label,fn in [('Auf Karte platzieren',lambda:self._dmr_pick_position(dlg,lat,lon,'Feststation' if fixed else 'Fahrzeugfunkgerät' if vehicle else 'Handfunkgerät',save)),
                         ('Speichern',save),('Löschen',delete),('Schließen',dlg.accept)]:
            button=QPushButton(label);button.clicked.connect(fn);row.addWidget(button)
        dlg.finished.connect(lambda _ : self._dmr_dialog_closed(dlg))
        refresh()
        if selected_id is not None:saved.setCurrentIndex(saved.findData(selected_id))
        dlg.show()

    def _dmr_dialog_closed(self,dlg):
        if self._dmr_map_pick and self._dmr_map_pick[0] is dlg:
            self._dmr_map_pick=None;self.map_mode='idle'

    def open_dmr_area(self):
        from .dmr_area import calculate_area
        dlg,layout=self._dmr_dialog_base('DMR Tier II – Flächenversorgung (Vorabschätzung)')
        if dlg is None:return
        notice=QLabel('Experimentelle Flächenkarte. Optional radiale Gelände-Beugungsabschätzung aus Online-Höhen (bis 72 Radiale, 33 Stützpunkte). Keine Gebäude-/Waldverluste oder statistische Versorgungswahrscheinlichkeit. Nicht als belastbare Funknetzplanung verwenden.')
        notice.setWordWrap(True);layout.addWidget(notice)
        reps=list(self.db.dmr_repeaters(self.project_id))
        rep=QComboBox(); [rep.addItem(r['name'],r['id']) for r in reps]
        form=QFormLayout();layout.addLayout(form)
        form.addRow('Repeater',rep)
        scenario=QComboBox();scenario.addItems(['Handfunkgerät im Freien (1,5 m)','Handfunkgerät im Gebäude (1,5 m)','Fahrzeugfunkgerät (2,0 m)','Feststation (10,0 m)'])
        form.addRow('Empfangsart / Antennenhöhe',scenario)
        scenario_info=QLabel('Empfangsantenne: 1,5 m über Grund; angenommener Gewinn 0 dBi, Sendeleistung 1 W. Diese Werte sind Szenarioannahmen.')
        scenario_info.setWordWrap(True);layout.addWidget(scenario_info)
        def update_scenario_info():
            descriptions=[
                'Handfunkgerät im Freien: 1,5 m über Grund, 0 dBi, 1 W, kein zusätzlicher Gebäudeverlust.',
                'Handfunkgerät im Gebäude: 1,5 m über Grund, 0 dBi, 1 W, zusätzlich 15 dB Gebäudeverlust.',
                'Fahrzeugfunkgerät: 2,0 m über Grund, 2 dBi, 10 W, 1 dB Leitungsverlust.',
                'Feststation: 10,0 m über Grund, 4 dBi, 10 W, 2 dB Leitungsverlust.']
            scenario_info.setText(descriptions[scenario.currentIndex()] + ' Die Antennenhöhe beeinflusst beide Geländemodelle.')
        scenario.currentIndexChanged.connect(lambda _:update_scenario_info())
        update_scenario_info()
        radius=self.spin(0.5,100,1,5);form.addRow('Radius (km)',radius)
        margin=self.spin(0,40,1,15);form.addRow('Erforderliche Reserve (dB)',margin)
        terrain=QComboBox();terrain.addItems(['Freiraum (ohne Gelände)', 'Gelände: radiale Beugungs-Vorabschätzung', 'Gelände: Einzelprofile je Rasterzelle (lokales DGM, langsam)', 'Longley-Rice/ITM (NTIA, lokales DGM, experimentell)']);form.addRow('Ausbreitungsmodell',terrain)
        mode=QComboBox();mode.addItems(['Pegelverlauf (relativ, dBm)','Downlink-Pegel (absolute dBm-Klassen)','Beidseitige Funkreserve (dB)'])
        mode.setCurrentIndex(1)  # Eight absolute dBm bands are the default.
        form.addRow('Kartendarstellung',mode)
        dbm_bands=load_bands(self.settings)
        resolution=QComboBox();resolution.addItems(['Standard (61 × 61)', 'Fein (101 × 101)', 'Sehr fein (121 × 121)', 'Detail (201 × 201)']);resolution.setCurrentIndex(1)
        form.addRow('Berechnungsraster',resolution)
        source=QComboBox();source.addItems(['Online-Höhen (Open-Meteo, mit Cache)', 'Lokale DGM-GeoTIFF (offline)', 'Automatisch: lokale DGM bevorzugen, Copernicus bei Lücken'])
        form.addRow('Geländedatenquelle',source)
        source_info=QLabel('Offline: Zuerst links im Hauptfenster einen DGM-Ordner (GeoTIFF) auswählen. Keine Online-Anfragen.')
        source_info.setWordWrap(True);layout.addWidget(source_info)

        hint=QLabel('Die Karte wird als zusammenhängendes Raster statt Tausender Rechtecke gezeichnet. Im Modus Funkreserve bleiben nicht versorgte Zellen transparent. Höhere Kartendichte erhöht NICHT die Geländeprofil-Auflösung (72 × 33).');hint.setWordWrap(True);layout.addWidget(hint)
        legend=QLabel('Acht frei einstellbare Downlink-dBm-Klassen.')
        legend.setWordWrap(True);layout.addWidget(legend)
        def update_legend():
            if mode.currentIndex()==0:
                legend.setText('Relativer Pegelverlauf: Rot = niedrigster, Dunkelgrün = höchster berechneter Downlink-Pegel. Farben sind KEINE Versorgungsaussage.')
            elif mode.currentIndex()==1:
                legend.setText('Downlink-dBm-Farbpalette: ' + ' | '.join(f"{b['label']} ab {b['min']:g} dBm ({b['color']})" for b in dbm_bands) + f". Unter {dbm_bands[-1]['min']:g} dBm transparent.")
            else:
                legend.setText('Beidseitige Reserve relativ zur Zielreserve: unter -12 / -12 bis -6 / -6 bis 0 / 0 bis +12 / ab +12 dB. Farben: Rot, Orange, Gelb, Hellgrün, Dunkelgrün.')
        mode.currentIndexChanged.connect(lambda _:update_legend())
        update_legend()
        result=QPlainTextEdit();result.setReadOnly(True);result.setMinimumHeight(110)
        result.setPlainText('Noch keine Berechnung.');layout.addWidget(result)
        def show_result(message):
            result.setPlainText(message)
            if 'fehlgeschlagen' in message.lower():
                self.diagnostic_messages.append(message)
        def copy_result():
            QApplication.clipboard().setText(result.toPlainText())
            self.statusBar().showMessage('Meldung in die Zwischenablage kopiert.',5000)
        copy_button=QPushButton('Meldung kopieren (Strg+C auch möglich)')
        copy_button.clicked.connect(copy_result);layout.addWidget(copy_button)
        def show_diagnostics():
            diag=QDialog(dlg);diag.setWindowTitle('Meldungen und Diagnose (aktuelle Sitzung)');diag.resize(700,400)
            diag_layout=QVBoxLayout(diag)
            text=QPlainTextEdit();text.setReadOnly(True)
            text.setPlainText('\n\n---\n\n'.join(self.diagnostic_messages) or 'Noch keine Fehlermeldungen.')
            diag_layout.addWidget(text)
            copy_all=QPushButton('Alle Meldungen kopieren')
            copy_all.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
            diag_layout.addWidget(copy_all);diag.exec()
        diag_button=QPushButton('Meldungen und Diagnose anzeigen')
        diag_button.clicked.connect(show_diagnostics);layout.addWidget(diag_button)


        last_missing=[]

        def diagnose_missing():
            from .terrain import TerrainSampler, TerrainError
            if not self.terrain_folder:
                show_result('Bitte zuerst einen lokalen DGM-Ordner auswählen.')
                return
            r = next((r for r in reps if r['id']==rep.currentData()),None)
            if r is None:
                show_result('Bitte zuerst einen DMR-Repeater auswählen.')
                return
            import math
            lat=float(r['latitude']); lon=float(r['longitude']); radius_km=radius.value()
            coords=[]
            for ray in range(72):
                angle=2*math.pi*ray/72
                for i in range(33):
                    km=radius_km*i/32
                    coords.append((lat+km*math.cos(angle)/111.2,
                                   lon+km*math.sin(angle)/(111.2*max(.15,math.cos(math.radians(lat))))))
            from PySide6.QtWidgets import QProgressDialog
            last_missing.clear()
            progress=QProgressDialog('Geländeabdeckung wird geprüft ...','Abbrechen',0,len(coords),dlg)
            progress.setMinimumDuration(0)
            missing=[]; nodata=[]; valid=0
            try:
                with TerrainSampler(self.terrain_folder) as sampler:
                    for i,(a,b) in enumerate(coords):
                        if i%16==0:
                            progress.setValue(i)
                            QApplication.processEvents()
                            if progress.wasCanceled():
                                show_result('DGM-Diagnose abgebrochen.')
                                return
                        try:
                            sampler.sample(a,b)
                            valid+=1
                        except TerrainError:
                            detail=sampler.diagnose(a,b)
                            (nodata if detail['status']=='nodata' else missing).append(detail)
                progress.setValue(len(coords))
                last_missing.extend((d["latitude"],d["longitude"]) for d in missing)
                lines=[f'DGM-Diagnose für {radius_km:g} km Radius:',
                       f'{valid} gültige Höhenabfragen, {len(missing)} ohne Kachelabdeckung, {len(nodata)} ohne gültigen Rasterwert.',
                       '']
                for label,items in [('Ohne Kachelabdeckung',missing),('Kachel vorhanden, aber NoData/Lesefehler',nodata)]:
                    if items:
                        lines.append(label+':')
                        for d in items[:12]:
                            lines.append(f"  {d['latitude']:.6f} N, {d['longitude']:.6f} E" +
                                         (f" – {d['matching'][0]['file']}" if d['matching'] else ''))
                        if len(items)>12:lines.append(f'  ... und {len(items)-12} weitere Punkte')
                if missing or nodata:
                    lines.append('')
                    lines.append('Fehlende Kacheln müssen über eine passende Datenquelle ergänzt werden. Ein automatischer amtlicher Kachel-Download ist noch nicht angebunden.')
                else:
                    lines.append('Alle abgefragten DGM-Punkte sind abgedeckt.')
                show_result('\n'.join(lines))
            except (TerrainError,ValueError) as exc:
                show_result('DGM-Diagnose fehlgeschlagen: '+str(exc))
            finally:
                progress.close()
        diagnose_button=QPushButton('DGM-Abdeckung für diesen Radius prüfen')
        diagnose_button.clicked.connect(diagnose_missing)
        layout.addWidget(diagnose_button)

        def offer_missing_download():
            from .dgm_catalog import load_catalog, matching_tiles, download_summary
            from .terrain_library import download_geotiff
            if not last_missing:
                QMessageBox.information(dlg,'Kachelkatalog','Bitte zuerst die DGM-Abdeckung prüfen. Nur fehlende Kacheln werden angeboten.')
                return
            catalog_path,_=QFileDialog.getOpenFileName(dlg,'GeoTIFF-Kachelkatalog (JSON) auswählen','','JSON-Katalog (*.json)')
            if not catalog_path:return
            try:
                candidates=matching_tiles(load_catalog(catalog_path),last_missing)
            except (ValueError,OSError,TypeError) as exc:
                self.alert('Katalogfehler: '+str(exc));return
            existing={p.name.lower() for p in __import__('radioplanner.terrain_library',fromlist=['dataset_files']).dataset_files(self.terrain_folder)}
            unique=[];seen=set()
            from urllib.parse import urlparse,unquote
            from pathlib import Path
            for tile in candidates:
                name=unquote(Path(urlparse(tile['url']).path).name).lower()
                if name not in existing and name not in seen:
                    unique.append(tile);seen.add(name)
            if not unique:
                QMessageBox.information(dlg,'Kachelkatalog','Keine neuen passenden GeoTIFF-Kacheln im gewählten Katalog gefunden.')
                return
            message=('Gefundene fehlende DGM-Kacheln: '+download_summary(unique)+'\n\n'
                     'Quelle und Lizenz des Katalogs bitte prüfen. Die Größenangaben stammen aus dem Katalog und können fehlen.\n'
                     'Jetzt diese GeoTIFFs herunterladen?')
            if QMessageBox.question(dlg,'Download bestätigen',message,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
            progress=QProgressDialog('DGM-Kacheln werden heruntergeladen ...','Abbrechen',0,len(unique),dlg)
            progress.setWindowModality(Qt.WindowModal);progress.setMinimumDuration(0)
            def task(emit,cancel):
                downloaded=0;skipped=0
                for i,tile in enumerate(unique):
                    if cancel.is_set():raise InterruptedError('Download abgebrochen.')
                    try:
                        download_geotiff(tile['url'],self.terrain_folder,cancelled=cancel.is_set)
                        downloaded+=1
                    except FileExistsError:
                        skipped+=1
                    emit(str(i+1))
                return (downloaded,skipped)
            worker=CalculationWorker(task,dlg)
            worker.progress.connect(lambda count:progress.setValue(int(count)))
            def done(stats):
                progress.close();last_missing.clear()
                QMessageBox.information(dlg,'DGM-Download',f'{stats[0]} Kacheln heruntergeladen, {stats[1]} übersprungen.\nBitte die DGM-Diagnose erneut ausführen.')
            worker.completed.connect(done)
            worker.failed.connect(lambda error:(progress.close(),self.alert('DGM-Download fehlgeschlagen: '+error)))
            progress.canceled.connect(worker.cancel_event.set)
            worker.finished.connect(worker.deleteLater)
            if not hasattr(dlg,'_catalog_workers'):dlg._catalog_workers=[]
            dlg._catalog_workers.append(worker)
            worker.start()
        def nationwide_download():
            from .germany_dem import missing_tiles, remote_size
            from .terrain_library import download_geotiff
            if not self.terrain_folder:
                QMessageBox.warning(dlg,'Geländedaten','Bitte zuerst einen lokalen DGM-Ordner festlegen.')
                return
            if not last_missing:
                QMessageBox.information(dlg,'Geländedaten','Bitte zuerst die DGM-Abdeckung prüfen. Fehlende Bereiche werden gezielt ergänzt.')
                return
            tiles=missing_tiles(last_missing,self.terrain_folder)
            if not tiles:
                QMessageBox.information(dlg,'Geländedaten','Alle benötigten Copernicus-Kacheln sind bereits lokal vorhanden. Bitte Diagnose wiederholen.')
                return
            # HEAD requests are intentionally performed in the background.
            progress=QProgressDialog('Online-Kacheln werden auf Verfügbarkeit geprüft ...','Abbrechen',0,len(tiles),dlg)
            progress.setMinimumDuration(0)
            def check_task(emit,cancel):
                available=[];errors=[]
                for i,(name,url) in enumerate(tiles):
                    if cancel.is_set():raise InterruptedError('Prüfung abgebrochen.')
                    try:available.append((name,url,remote_size(url)))
                    except (ValueError,OSError) as exc:errors.append(f'{name}: {exc}')
                    emit(str(i+1))
                return available,errors
            worker=CalculationWorker(check_task,dlg)
            worker.progress.connect(lambda value:progress.setValue(int(value)))
            progress.canceled.connect(worker.cancel_event.set)
            def checked(result):
                progress.close()
                available,errors=result
                if not available:
                    QMessageBox.warning(dlg,'Datenquelle nicht erreichbar','Keine Kacheln erreichbar.\n'+ '\n'.join(errors[:4]))
                    return
                known=sum(x[2] for x in available if x[2] is not None)
                unknown=sum(x[2] is None for x in available)
                size=f'{known/1024/1024:.1f} MiB bekannt' + (f', {unknown} Größe(n) unbekannt' if unknown else '')
                message=(f'{len(available)} Copernicus-GLO-30-Kacheln verfügbar.\n'
                         f'Downloadgröße: {size}\n'
                         f'Speicherort: {self.terrain_folder}\n\n'
                         'ACHTUNG: Copernicus GLO-30 ist ein Oberflächenmodell (ca. 30 m), KEIN amtliches DGM1. '
                         'Gebäude und Vegetation können Höhen beeinflussen. Bereits vorhandene lokale DGM1-Dateien bleiben bevorzugt.\n\n'
                         'Jetzt herunterladen?')
                if QMessageBox.question(dlg,'Download bestätigen',message,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
                dlprogress=QProgressDialog('Copernicus-Geländedaten werden geladen ...','Abbrechen',0,len(available),dlg)
                dlprogress.setMinimumDuration(0)
                def download_task(emit,cancel):
                    downloaded=0;skipped=0
                    for i,(name,url,_) in enumerate(available):
                        if cancel.is_set():raise InterruptedError('Download abgebrochen.')
                        try:download_geotiff(url,self.terrain_folder,cancelled=cancel.is_set);downloaded+=1
                        except FileExistsError:skipped+=1
                        emit(str(i+1))
                    return downloaded,skipped
                dlworker=CalculationWorker(download_task,dlg)
                dlworker.progress.connect(lambda value:dlprogress.setValue(int(value)))
                dlprogress.canceled.connect(dlworker.cancel_event.set)
                def finished(stats):
                    dlprogress.close();last_missing.clear()
                    QMessageBox.information(dlg,'Geländedaten',f'{stats[0]} Kacheln geladen, {stats[1]} übersprungen.\nBitte DGM-Abdeckung erneut prüfen.')
                dlworker.completed.connect(finished)
                dlworker.failed.connect(lambda error:(dlprogress.close(),self.alert('Geländedaten-Download: '+error)))
                dlworker.finished.connect(dlworker.deleteLater)
                dlg._nationwide_workers.append(dlworker)
                dlworker.start()
                if errors:show_result('Einige Kacheln nicht verfügbar: '+ '\n'.join(errors[:5]))
            worker.completed.connect(checked)
            worker.failed.connect(lambda error:(progress.close(),self.alert('Kachelprüfung: '+error)))
            worker.finished.connect(worker.deleteLater)
            if not hasattr(dlg,'_nationwide_workers'):dlg._nationwide_workers=[]
            dlg._nationwide_workers.append(worker)
            worker.start()
        nationwide_button=QPushButton('Deutschlandweit fehlende Geländedaten suchen und nach Bestätigung laden ...')
        nationwide_button.clicked.connect(nationwide_download)
        layout.addWidget(nationwide_button)

        catalog_button=QPushButton('Fehlende DGM-Kacheln aus JSON-Katalog nachladen ...')
        catalog_button.clicked.connect(offer_missing_download)
        layout.addWidget(catalog_button)

        def calculate():
            r=next((r for r in reps if r['id']==rep.currentData()),None)
            if r is None:return self.alert('Bitte zuerst einen DMR-Repeater speichern.')
            presets=[(1.0,0.0,0.0,-115.0,0.0),(1.0,0.0,0.0,-115.0,15.0),(10.0,2.0,1.0,-115.0,0.0),(10.0,4.0,2.0,-115.0,0.0)]
            # Increase grid density moderately for large areas without quadratic explosion.
            steps=(61,101,121,201)[resolution.currentIndex()]
            loss_fn=None
            if terrain.currentIndex()==1:
                from .dmr_area_terrain import radial_model
                from .terrain import TerrainError
                from PySide6.QtWidgets import QApplication
                show_result('Geländehöhen werden geladen. Bitte warten ...')
                QApplication.processEvents()
                try:
                    if source.currentIndex() in (1,2) and not self.terrain_folder:
                        raise TerrainError('Kein lokaler DGM-Ordner ausgewählt. Bitte im Hauptfenster einen DGM-Ordner (GeoTIFF) wählen.')
                    progress_dialog=None
                    progress_callback=None
                    if source.currentIndex() in (1,2):
                        from PySide6.QtWidgets import QProgressDialog
                        progress_dialog=QProgressDialog('DGM-Höhen werden ausgewertet ...','Abbrechen',0,72*33,dlg)
                        progress_dialog.setWindowTitle('Lokale Geländeberechnung')
                        progress_dialog.setMinimumDuration(0)
                        def progress_callback(done,total):
                            progress_dialog.setMaximum(total)
                            progress_dialog.setValue(done)
                            QApplication.processEvents()
                            if progress_dialog.wasCanceled():
                                raise TerrainError('Geländeberechnung vom Benutzer abgebrochen.')
                    try:
                        model=radial_model(dict(r),radius.value(),self.online_cache,rays=72,samples=33,
                                           dgm_folder=self.terrain_folder if source.currentIndex() in (1,2) else None,
                                           progress=progress_callback)
                    finally:
                        if progress_dialog is not None:progress_dialog.close()
                    rx_height=(1.5,1.5,2.0,10.0)[scenario.currentIndex()]
                    loss_fn=lambda a,b,d:model(a,b,d,rx_height)
                except (TerrainError,ValueError) as exc:
                    show_result(f'Geländeberechnung fehlgeschlagen: {exc}\nBitte auf „DGM-Abdeckung für diesen Radius prüfen“ klicken. Fehlende Höhenwerte werden nicht durch erfundene Werte ersetzt.')
                    return
            close_point_model=None
            point_progress=None
            if terrain.currentIndex()==2:
                from .terrain import TerrainError
                from .dmr_point_terrain import point_model
                from PySide6.QtWidgets import QApplication, QProgressDialog
                if source.currentIndex()==0 or not self.terrain_folder:
                    return self.alert('Einzelprofile benötigen einen lokalen DGM-Ordner. Bitte als Höhenquelle lokale DGM-Daten wählen.')
                if steps>61:
                    steps=61
                    show_result('Einzelprofile: Raster aus Leistungsgründen auf 61 × 61 begrenzt.\nGeländehöhen werden ausgewertet ...')
                rx_height=(1.5,1.5,2.0,10.0)[scenario.currentIndex()]
                try:
                    loss_fn,close_point_model=point_model(dict(r),self.terrain_folder,rx_height=rx_height)
                except (TerrainError,ValueError) as exc:
                    return self.alert('Einzelprofil-Modell: '+str(exc))
                point_progress=QProgressDialog('Gelände-Einzelprofile werden berechnet ...','Abbrechen',0,steps*steps,dlg)
                point_progress.setWindowTitle('Geländeberechnung')
                point_progress.setMinimumDuration(0)
                counter=[0]
                base_loss_fn=loss_fn
                def loss_fn(a,b,d):
                    counter[0]+=1
                    if counter[0]%16==0:
                        point_progress.setValue(counter[0])
                        QApplication.processEvents()
                        if point_progress.wasCanceled():
                            raise TerrainError('Berechnung abgebrochen.')
                    return base_loss_fn(a,b,d)
            # Native NTIA ITM returns TOTAL path loss; never combine with diffraction loss.
            itm_close = None
            itm_path_loss = None
            if terrain.currentIndex() == 3:
                from pathlib import Path
                from .itm_native import NativeITM
                from .itm_adapter import create_itm_path_loss, ITMSettings
                from PySide6.QtWidgets import QProgressDialog, QApplication
                if not self.terrain_folder:
                    return self.alert('ITM benötigt lokale DGM-GeoTIFF-Daten. Bitte zuerst einen DGM-Ordner auswählen.')
                import sys
                library_name = 'rp_itm.dll' if sys.platform == 'win32' else 'librp_itm.so'
                candidates = [Path(__file__).resolve().parent.parent / 'native_itm' / library_name,
                              Path(__file__).resolve().parent.parent / library_name]
                library = next((p for p in candidates if p.is_file()), None)
                if library is None:
                    return self.alert('Die native NTIA-ITM-Bibliothek fehlt (' + library_name + ').\n'
                                      'Bitte gemäß native_itm/BUILD.md für Windows kompilieren.\n'
                                      'Es wird NICHT auf ein anderes Modell zurückgegriffen.')
                steps = min(steps, 41)  # ITM requires two terrain profiles per cell.
                rx_height = (1.5, 1.5, 2.0, 10.0)[scenario.currentIndex()]
                try:
                    itm_path_loss, itm_close = create_itm_path_loss(
                        dict(r), self.terrain_folder, NativeITM(library),
                        rx_height_m=rx_height, spacing_m=180.0, settings=ITMSettings())
                except Exception as exc:
                    return self.alert('ITM konnte nicht gestartet werden: ' + str(exc))
                itm_progress = QProgressDialog('NTIA-ITM berechnet Geländeprofile ...', 'Abbrechen', 0, steps*steps*2, dlg)
                itm_progress.setMinimumDuration(0)
                itm_counter = [0]
                original_itm_path_loss = itm_path_loss
                def itm_path_loss(a, b, d, freq):
                    itm_counter[0] += 1
                    if itm_counter[0] % 8 == 0:
                        itm_progress.setValue(itm_counter[0])
                        QApplication.processEvents()
                        if itm_progress.wasCanceled():
                            raise RuntimeError('ITM-Berechnung abgebrochen.')
                    return original_itm_path_loss(a, b, d, freq)
            else:
                itm_progress = None
            import time
            calculation_start=time.perf_counter()
            dbm_bands[:]=load_bands(self.settings)
            update_legend()
            try:
                cells,stats=calculate_area(dict(r),presets[scenario.currentIndex()],radius.value(),margin.value(),steps, ('relative','level','margin')[mode.currentIndex()],terrain_loss_fn=loss_fn,dbm_bands=dbm_bands if mode.currentIndex()==1 else None,path_loss_fn=itm_path_loss)
            except Exception as exc:
                self.alert('Flächenberechnung fehlgeschlagen: '+str(exc))
                return
            finally:
                if itm_close is not None:itm_close()
                if itm_progress is not None:itm_progress.close()
                if close_point_model is not None:close_point_model()
                if point_progress is not None:point_progress.close()
            calculation_seconds=time.perf_counter()-calculation_start
            self.map.set_dmr_area_coverage(cells, coverage_only=(mode.currentIndex()==2),palette_bands=dbm_bands if mode.currentIndex()==1 else None)
            self.last_dmr_result={'name':'Letzte DMR-Berechnung','cells':cells,'metadata':{
                'scenario':scenario.currentText(),'terrain':terrain.currentText(),
                'radius_km':radius.value(),'required_margin_db':margin.value(),
                'resolution':steps,'mode':mode.currentText(),
                'coverage_only':mode.currentIndex()==2,
                'palette_bands':[dict(b) for b in dbm_bands] if mode.currentIndex()==1 else None,
                'repeater':dict(r),'calculation_seconds':round(calculation_seconds,1),
                'timestamp':datetime.now().isoformat(timespec='seconds'),
                'summary':(f'{stats["count"]} Rasterzellen; Downlink {stats["min_dbm"]:.1f} bis {stats["max_dbm"]:.1f} dBm; '
                           f'beidseitig {stats["good"]} ausreichend, {stats["border"]} grenzwertig, {stats["poor"]} unzureichend; '
                           f'Farbklassen {stats["band_counts"]}; Rechenzeit {calculation_seconds:.1f} s.')
            }}
            show_result(f'{stats["count"]} Rasterzellen, Downlink-Pegel {stats["min_dbm"]:.1f} bis {stats["max_dbm"]:.1f} dBm.\n'
                           f'Beidseitige Versorgung (mit gewählter Geländeabschattung): {stats["good"]} ausreichend, {stats["border"]} grenzwertig, {stats["poor"]} unzureichend.\n'
                           f'Empfangsart: {scenario.currentText()}; Geländemodell: {terrain.currentText()}.\n'
                           f'Darstellungsmodus: {mode.currentText()} ({len(stats["band_counts"])} Klassen).\n'
                           f'Farbklassen: {stats["band_counts"]}; unter niedrigster dBm-Grenze: {stats["below_minimum"]}.\n'
                           f'Geländedämpfung: {stats["min_terrain_loss_db"]:.1f} bis {stats["max_terrain_loss_db"]:.1f} dB.\n'
                           f'Rechenzeit Funkfeld: {calculation_seconds:.1f} Sekunden.\n'
                           + ('NTIA-ITM: vollständiger Pfadverlust; noch nicht gegen Radio Mobile validiert.' if itm_path_loss else (('Einzelprofile: dominante Beugungskante, keine validierte Versorgung.' if terrain.currentIndex()==2 else 'Radiale Geländebeugungs-Vorabschätzung, keine validierte Versorgung.') if loss_fn else 'Nur Freiraum-Vorabschätzung ohne Geländeabschattung.')))
            self.statusBar().showMessage('DMR-Flächenvorabschätzung auf Karte angezeigt.',8000)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        for title,action in [('Berechnen und auf Karte anzeigen',calculate),('Darstellung löschen',lambda:self.map.set_dmr_area_coverage([])),('Schließen',dlg.accept)]:
            btn=QPushButton(title);btn.clicked.connect(action);buttons.addWidget(btn)
        dlg.show()

    def open_dmr_coverage(self):
        from .dmr import evaluate_link,distance_km
        dlg,layout=self._dmr_dialog_base('DMR Tier II – Funkversorgung und Geländeprofil')
        if dlg is None:return
        note=QLabel('Funkpegel: Freiraumdämpfung (FSPL). Optional: Gelände-Sichtlinienprüfung (keine zusätzliche Geländedämpfung, keine Flächenprognose).')
        note.setWordWrap(True);layout.addWidget(note)
        repeater=QComboBox();device=QComboBox()
        layout.addWidget(QLabel('Repeater'));layout.addWidget(repeater)
        layout.addWidget(QLabel('Funkgerät'));layout.addWidget(device)
        reps=list(self.db.dmr_repeaters(self.project_id));devices=list(self.db.dmr_devices(self.project_id))
        for r in reps:repeater.addItem(r['name'],r['id'])
        for r in devices:device.addItem(r['name'],r['id'])
        reserve=self.spin(0,50,1,15)
        form=QFormLayout();layout.addLayout(form)
        form.addRow('Zielreserve dB',reserve)
        result=QPlainTextEdit();result.setReadOnly(True);layout.addWidget(result)
        def calc():
            r=next((r for r in reps if r['id']==repeater.currentData()),None)
            d=next((d for d in devices if d['id']==device.currentData()),None)
            if r is None or d is None:return self.alert('Bitte Repeater und Funkgerät speichern und auswählen.')
            distance=distance_km(r['latitude'],r['longitude'],d['latitude'],d['longitude'])
            data=evaluate_link(distance,r['frequency_mhz'],r['power_w'],r['gain_dbi'],r['loss_db'],
                d['power_w'],d['gain_dbi'],d['loss_db'],d['rx_sensitivity_dbm'],reserve.value(),
                15 if d['device_type']=='handheld_indoor' else 0,
                repeater_sensitivity_dbm=r['rx_sensitivity_dbm'])
            result.setPlainText(f"Entfernung: {distance:.2f} km\nDownlink: {data['downlink_dbm']:.1f} dBm (Reserve {data['downlink_margin_db']:.1f} dB)\nUplink: {data['uplink_dbm']:.1f} dBm (Reserve {data['uplink_margin_db']:.1f} dB)\nBeidseitig nutzbar (Modell): {'Ja' if data['usable'] else 'Nein'}\n\nNur FSPL-Vorabschätzung! Antennenhöhen werden gespeichert, aber in FSPL noch nicht berücksichtigt.")
        def terrain_check():
            from .dmr_terrain import sample_path, terrain_clearance
            from .online_terrain import elevations, SOURCE
            from .terrain import TerrainError
            r=next((r for r in reps if r['id']==repeater.currentData()),None)
            d=next((d for d in devices if d['id']==device.currentData()),None)
            if r is None or d is None:return self.alert('Bitte Repeater und Funkgerät auswählen.')
            pts=sample_path(r['latitude'],r['longitude'],d['latitude'],d['longitude'])
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try: heights=elevations(pts,self.online_cache)
                finally: QApplication.restoreOverrideCursor()
                assessment=terrain_clearance(heights,r['height_m'],d['height_m'],
                    distance_km(r['latitude'],r['longitude'],d['latitude'],d['longitude']),r['frequency_mhz'])
                result.setPlainText(
                    f'Geländeprofil ({len(heights)} Stützpunkte)\nQuelle: {SOURCE}\n'
                    f'Entfernung: {assessment["distance_km"]:.2f} km\n'
                    f'Minimale Sichtlinienfreiheit: {assessment["min_los_m"]:.1f} m\n'
                    f'Minimale Freiheit zu 60 % der ersten Fresnelzone: {assessment["min_fresnel60_m"]:.1f} m\n'
                    f'Bewertung: {assessment["status"]}\n\n'
                    'Nur Gelände-Screening mit Erdkrümmung (k=4/3). Keine Beugungsverlustrechnung, '
                    'keine Vorhersage der Versorgungswahrscheinlichkeit. Höhenbezug prüfen; '
                    '90-m-Geländemodell kann Hindernisse und Gebäude übersehen. '
                    'Uplink-/Downlink-Pegel separat über die FSPL-Schaltfläche berechnen.')
            except (TerrainError,ValueError) as exc:self.alert(str(exc))
        row=QHBoxLayout();layout.addLayout(row)
        for label,fn in [('Uplink / Downlink berechnen',calc),('Geländeprofil prüfen',terrain_check),('Schließen',dlg.accept)]:
            button=QPushButton(label);button.clicked.connect(fn);row.addWidget(button)
        dlg.show()

    def open_dbm_palette_settings(self):
        """Globale, unabhängig von einer Flächenberechnung verfügbare dBm-Farbpalette."""
        editor=QDialog(self)
        editor.setWindowTitle('Einstellungen – Funkversorgung – dBm-Farbpalette')
        editor.resize(650, 480)
        editor_layout=QVBoxLayout(editor)
        note=QLabel('Acht Downlink-Pegelklassen (dBm), von stark nach schwach. '
                    'Unterhalb der niedrigsten Grenze bleibt die Karte transparent.')
        note.setWordWrap(True)
        editor_layout.addWidget(note)
        grid=QFormLayout()
        editor_layout.addLayout(grid)
        controls=[]
        for index,band in enumerate(load_bands(self.settings),1):
            row=QHBoxLayout()
            threshold=QDoubleSpinBox()
            threshold.setRange(-200,100)
            threshold.setDecimals(1)
            threshold.setSingleStep(1)
            threshold.setSuffix(' dBm')
            threshold.setValue(band['min'])
            name=QLineEdit(band['label'])
            name.setMaximumWidth(190)
            color_button=QPushButton()
            color_button.setMinimumWidth(100)
            chosen=[band['color']]
            def show_color(button=color_button,selected=chosen):
                color=selected[0]
                button.setText(color)
                button.setStyleSheet('background-color: '+color+'; color: '+
                                     ('#FFFFFF' if QColor(color).lightness()<130 else '#000000'))
            show_color()
            def pick_color(checked=False,selected=chosen,refresh=show_color):
                color=QColorDialog.getColor(QColor(selected[0]),editor,'Farbe wählen')
                if color.isValid():
                    selected[0]=color.name().upper()
                    refresh()
            color_button.clicked.connect(pick_color)
            row.addWidget(threshold)
            row.addWidget(name)
            row.addWidget(color_button)
            grid.addRow('Stufe '+str(index),row)
            controls.append((threshold,name,chosen,show_color))
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        reset=QPushButton('Standardwerte wiederherstellen')
        buttons.addButton(reset,QDialogButtonBox.ResetRole)
        def restore():
            for (spin,name,color,refresh),band in zip(controls,DEFAULT_BANDS):
                spin.setValue(band['min'])
                name.setText(band['label'])
                color[0]=band['color']
                refresh()
        reset.clicked.connect(restore)
        def save():
            try:
                bands=validate_bands([{'min':spin.value(),'label':name.text(),'color':color[0]}
                                      for spin,name,color,_ in controls])
            except ValueError as exc:
                QMessageBox.warning(editor,'Ungültige Farbpalette',str(exc))
                return
            self.settings.setValue('dmr/dbm_bands',json.dumps(bands,ensure_ascii=False))
            self.settings.sync()
            editor.accept()
        buttons.accepted.connect(save)
        buttons.rejected.connect(editor.reject)
        editor_layout.addWidget(buttons)
        editor.exec()

    def open_equipment_settings(self):
        library=EquipmentLibrary()
        dlg=QDialog(self);dlg.setWindowTitle('Einstellungen – Geräte und Antennen');dlg.resize(670,510)
        layout=QVBoxLayout(dlg)
        description=QLabel('Eigene Datenblätter verwalten. Muster sind keine realen Herstellerangaben.')
        description.setWordWrap(True);layout.addWidget(description)
        category=QComboBox();category.addItems(['Antenne','Funkgerät']);layout.addWidget(category)
        entries=QComboBox();layout.addWidget(entries)
        form=QFormLayout();layout.addLayout(form)
        maker=QLineEdit();model=QLineEdit();form.addRow('Hersteller',maker);form.addRow('Modell',model)
        freq_min=self.spin(100,100000,1,5000);freq_max=self.spin(100,100000,1,6000)
        form.addRow('Frequenz von (MHz)',freq_min);form.addRow('Frequenz bis (MHz)',freq_max)
        gain=self.spin(-30,80,2,27);beam=self.spin(0,360,1,10)
        power=self.spin(-100,100,2,20);sensitivity=self.spin(-160,0,2,-75)
        polarization=QComboBox();polarization.addItems(['Horizontal','Vertikal','Dual','Zirkular','Unbekannt'])
        form.addRow('Antennengewinn (dBi)',gain);form.addRow('Öffnungswinkel (°)',beam)
        form.addRow('Polarisation',polarization);form.addRow('Sendeleistung (dBm)',power)
        form.addRow('Empfängerempfindlichkeit (dBm)',sensitivity)
        hint=QLabel('Änderungen an Geräten/Antennen beeinflussen bestehende Strecken erst nach erneuter Auswahl.')
        hint.setWordWrap(True);layout.addWidget(hint)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        add=QPushButton('Neu');save=QPushButton('Speichern');delete=QPushButton('Löschen');close=QPushButton('Schließen')
        for w in (add,save,delete,close):buttons.addWidget(w)
        def refresh(selected=None):
            entries.blockSignals(True);entries.clear();entries.addItem('Neuer Eintrag',None)
            for item in library.list(category.currentText()):entries.addItem(item['manufacturer']+' – '+item['model']+(' [Muster]' if item.get('example') else ''),item['id'])
            if selected:
                index=entries.findData(selected)
                if index>=0:entries.setCurrentIndex(index)
            entries.blockSignals(False)
            load()
        def load(*args):
            item=library.get(entries.currentData()) if entries.currentData() else None
            maker.setText(item['manufacturer'] if item else '')
            model.setText(item['model'] if item else '')
            freq_min.setValue(item['frequency_min_mhz'] if item else 5000)
            freq_max.setValue(item['frequency_max_mhz'] if item else 6000)
            gain.setValue(item.get('gain_dbi',27) if item else 27)
            beam.setValue(item.get('beamwidth_deg',10) if item else 10)
            power.setValue(item.get('tx_power_dbm',20) if item else 20)
            sensitivity.setValue(item.get('rx_sensitivity_dbm',-75) if item else -75)
            polarization.setCurrentText(item.get('polarization','Unbekannt') if item else 'Unbekannt')
            is_antenna=category.currentText()=='Antenne'
            for w in (gain,beam,polarization):w.setEnabled(is_antenna)
            for w in (power,sensitivity):w.setEnabled(not is_antenna)
            delete.setEnabled(bool(item) and not item.get('example'))
        def store():
            data=dict(category=category.currentText(),manufacturer=maker.text().strip(),model=model.text().strip(),
                      frequency_min_mhz=freq_min.value(),frequency_max_mhz=freq_max.value(),
                      gain_dbi=gain.value(),beamwidth_deg=beam.value(),polarization=polarization.currentText(),
                      tx_power_dbm=power.value(),rx_sensitivity_dbm=sensitivity.value())
            try:
                item=library.get(entries.currentData())
                key=library.save(data,None if item and item.get('example') else entries.currentData())
                refresh(key)
            except ValueError as exc:QMessageBox.warning(dlg,'Ungültige Angaben',str(exc))
        def remove():
            try:library.delete(entries.currentData());refresh()
            except ValueError as exc:QMessageBox.warning(dlg,'Nicht möglich',str(exc))
        category.currentTextChanged.connect(lambda _:refresh())
        entries.currentIndexChanged.connect(load)
        add.clicked.connect(lambda:entries.setCurrentIndex(0));save.clicked.connect(store)
        delete.clicked.connect(remove);close.clicked.connect(dlg.accept)
        refresh();dlg.exec()

    def open_symbol_settings(self):
        dlg=QDialog(self);dlg.setWindowTitle('Einstellungen – Kartensymbole');dlg.resize(530,430)
        outer=QVBoxLayout(dlg)
        tabs=QComboBox();tabs.addItems(['Senderstandorte','Sirenenstandorte','Richtfunk']);outer.addWidget(tabs)
        form=QFormLayout();outer.addLayout(form)
        choice=QComboBox();form.addRow('Symbol:',choice)
        preview=QLabel();form.addRow('Vorschau:',preview)
        size=QSpinBox();size.setRange(16,64);size.setSuffix(' px');form.addRow('Größe:',size)
        color_button=QPushButton('Farbe auswählen ...');form.addRow('Farbe:',color_button)
        custom_button=QPushButton('Eigenes PNG/SVG auswählen ...');form.addRow('Eigenes Symbol:',custom_button)
        clear_button=QPushButton('Eigenes Symbol entfernen');form.addRow('',clear_button)
        notice=QLabel('Die Symbole ändern nur die Kartendarstellung, nicht die Berechnung.\nRichtfunk-Symbole sind für eine spätere Darstellung einzelner Links vorbereitet.')
        notice.setWordWrap(True);outer.addWidget(notice)
        outer.addStretch(1)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel|QDialogButtonBox.RestoreDefaults)
        buttons.button(QDialogButtonBox.Save).setText('Speichern')
        buttons.button(QDialogButtonBox.Cancel).setText('Abbrechen')
        buttons.button(QDialogButtonBox.RestoreDefaults).setText('Standard wiederherstellen')
        outer.addWidget(buttons)
        current=self._symbol_settings();keys=['sender','siren','link'];state={'loading':False}
        def key():return keys[tabs.currentIndex()]
        def update_preview():
            v=current[key()];preview.setText(('Eigenes Symbol' if v['custom'] else next((n for n,k in self.SYMBOL_CHOICES[key()] if k==v['shape']),'Standortmarker'))+f" | {v['size']} px | {v['color']}")
        def load():
            state['loading']=True;v=current[key()];choice.clear()
            for name,code in self.SYMBOL_CHOICES[key()]:choice.addItem(name,code)
            choice.setCurrentIndex(max(0,choice.findData(v['shape'])));size.setValue(v['size']);update_preview();state['loading']=False
        def change_shape(i):
            if state['loading'] or i<0:return
            current[key()]['shape']=choice.currentData();current[key()]['custom']='';update_preview()
        def change_size(value):
            if not state['loading']:current[key()]['size']=value;update_preview()
        def change_color():
            c=QColorDialog.getColor(parent=dlg)
            if c.isValid():current[key()]['color']=c.name();update_preview()
        def custom():
            filename,_=QFileDialog.getOpenFileName(dlg,'Kartensymbol auswählen','','Bilder (*.png *.svg)')
            if not filename:return
            raw=Path(filename).read_bytes()
            if len(raw)>256*1024:
                QMessageBox.warning(dlg,'Symbol zu groß','Bitte eine PNG- oder SVG-Datei unter 256 KB wählen.');return
            suffix=Path(filename).suffix.lower()
            if suffix=='.svg':
                import xml.etree.ElementTree as ET
                try:
                    root=ET.fromstring(raw)
                    if not root.tag.endswith('svg') or any(x in raw.lower() for x in (b'<script',b'onload=',b'javascript:',b'<foreignobject',b'http://',b'https://')):raise ValueError('Unsicherer SVG-Inhalt')
                except Exception:
                    QMessageBox.warning(dlg,'Ungültige SVG-Datei','Bitte eine einfache SVG-Datei ohne Skripte oder externe Ressourcen wählen.');return
            else:
                if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    QMessageBox.warning(dlg,'Ungültige PNG-Datei','Die Datei ist kein gültiges PNG.');return
            mime='svg+xml' if suffix=='.svg' else 'png'
            current[key()]['custom']='data:image/'+mime+';base64,'+base64.b64encode(raw).decode('ascii');update_preview()
        def defaults():
            for k,v in {'sender':('#16803c','tower'),'siren':('#ea580c','megaphone'),'link':('#2563eb','dish')}.items():
                current[k]={'color':v[0],'shape':v[1],'size':30,'custom':''}
            load()
        tabs.currentIndexChanged.connect(lambda _:load())
        choice.currentIndexChanged.connect(change_shape);size.valueChanged.connect(change_size)
        color_button.clicked.connect(change_color);custom_button.clicked.connect(custom)
        clear_button.clicked.connect(lambda:(current[key()].update(custom=''),update_preview()))
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(defaults)
        buttons.accepted.connect(dlg.accept);buttons.rejected.connect(dlg.reject)
        load()
        if dlg.exec()==QDialog.Accepted:
            for k,v in current.items():
                for name,value in v.items():self.settings.setValue('symbols/'+k+'/'+name,value)
            self.map.apply_symbol_settings(current)

    def _setup_view_controls(self):
        menu = self.menuBar().addMenu('Ansicht')
        self.left_action = menu.addAction('Projektmenü ein-/ausblenden')
        self.left_action.triggered.connect(lambda: self.toggle_panel(0))
        self.right_action = None
        self.map_action = menu.addAction('Kartenmodus (F11)')
        self.map_action.setShortcut(QKeySequence('F11'))
        self.map_action.triggered.connect(self.toggle_map_mode)
        self.restore_action = menu.addAction('Standardansicht wiederherstellen')
        self.restore_action.triggered.connect(self.restore_default_layout)
        toolbar = self.addToolBar('Ansicht')
        toolbar.setMovable(False)
        toolbar.addAction(self.left_action)
        
        toolbar.addAction(self.map_action)
        saved = self.settings.value('splitter_sizes')
        if isinstance(saved, list) and len(saved) == 3:
            try:
                self.main_splitter.setSizes([int(v) for v in saved])
            except (ValueError, TypeError):
                pass
        self._previous_sizes = [290, 850, 310]

    def toggle_panel(self, index):
        if self.map_fullscreen:
            self.toggle_map_mode()
        sizes = self.main_splitter.sizes()
        if sizes[index] > 0:
            self._previous_sizes = sizes[:]
            sizes[1] += sizes[index]
            sizes[index] = 0
        else:
            preferred = max(220, self._previous_sizes[index])
            sizes[index] = preferred
            sizes[1] = max(200, sizes[1] - preferred)
        self.main_splitter.setSizes(sizes)

    def toggle_map_mode(self):
        if not self.map_fullscreen:
            self._previous_sizes = self.main_splitter.sizes()
            total = sum(self._previous_sizes)
            self.main_splitter.setSizes([0, total, 0])
            self.map_fullscreen = True
        else:
            self.main_splitter.setSizes(self._previous_sizes)
            self.map_fullscreen = False

    def restore_default_layout(self):
        self.map_fullscreen = False
        self.main_splitter.setSizes([290, 1160, 0])

    def closeEvent(self, event):
        if not self.map_fullscreen:
            self.settings.setValue('splitter_sizes', self.main_splitter.sizes())
        else:
            self.settings.setValue('splitter_sizes', self._previous_sizes)
        super().closeEvent(event)

    def spin(self,minimum,maximum,decimals,value):
        w=QDoubleSpinBox();w.setRange(minimum,maximum);w.setDecimals(decimals);w.setValue(value);return w

    def alert(self,error): QMessageBox.warning(self,'RadioPlanner',str(error))

    def reload_projects(self,select_id=None):
        if select_id is None: select_id=self.project_id
        self.projects.blockSignals(True);self.projects.clear()
        for p in self.db.projects():
            item=QListWidgetItem(f"{p['name']} ({p['station_count']}/100)")
            item.setData(Qt.UserRole,p['id']);self.projects.addItem(item)
            if p['id']==select_id:self.projects.setCurrentItem(item)
        self.projects.blockSignals(False)
        self.project_changed(self.projects.currentItem(),None)

    def add_project(self):
        name,ok=QInputDialog.getText(self,'Neues Projekt','Projektname:')
        if ok:
            try:self.reload_projects(self.db.create_project(name))
            except ValueError as e:self.alert(e)

    def rename_project(self):
        if self.project_id is None:return
        name,ok=QInputDialog.getText(self,'Projekt umbenennen','Neuer Name:',text=self.projects.currentItem().text().rsplit(' (',1)[0])
        if ok:
            try:self.db.rename_project(self.project_id,name);self.reload_projects()
            except ValueError as e:self.alert(e)

    def delete_project(self):
        if self.project_id is None:return
        if QMessageBox.question(self,'Projekt löschen','Projekt einschließlich aller Senderstandorte endgültig löschen?')==QMessageBox.Yes:
            self.db.delete_project(self.project_id);self.project_id=None;self.reload_projects()

    def project_changed(self,current,previous):
        self.project_id=current.data(Qt.UserRole) if current else None
        self.station_id=None
        self.network_freq.setEnabled(self.project_id is not None)
        if self.project_id is not None:
            self.network_freq.setValue(self.db.project(self.project_id)['network_frequency_mhz'])
        self.reload_stations()
        self.refresh_dmr_markers()
        self.reload_links()
        self.reload_sirens()
        self.update_frequency_control()

    def reload_stations(self,select_id=None):
        self.stations.blockSignals(True);self.stations.clear()
        records=self.db.stations(self.project_id) if self.project_id is not None else []
        for s in records:
            item=QListWidgetItem(s['name']);item.setData(Qt.UserRole,s['id']);self.stations.addItem(item)
            if s['id']==select_id:self.stations.setCurrentItem(item)
        self.stations.blockSignals(False)
        display_records=[]
        for station in records:
            item=dict(station)
            item['frequency_mhz']=self.db.effective_frequency(station)
            display_records.append(item)
        self.map.set_stations(display_records)
        if select_id is not None:self.station_changed(self.stations.currentItem(),None)
        else:self.station_id=None
        self.statusBar().showMessage(f'{len(records)} von 100 Senderstandorten im aktuellen Projekt')

    def new_station(self):
        if self.project_id is None:self.alert('Bitte zuerst ein Projekt anlegen.');return
        self.stations.blockSignals(True);self.stations.clearSelection();self.stations.setCurrentItem(None);self.stations.blockSignals(False)
        self.station_id=None
        self.name.setText('Neuer Sender');self.override.setChecked(False)
        self.freq.setValue(self.db.project(self.project_id)['network_frequency_mhz']);self.system.setCurrentText('POCSAG')
        self.terrain_result=None;self.terrain_source_label.setText('Quelle: manuell / unbekannt')
        self.terrain_known.setChecked(False);self.terrain.setValue(0);self.power.setValue(10)
        self.height.setValue(20);self.loss.setValue(1);self.antenna.setCurrentIndex(0);self.notes.clear()
        if self.pending_coords:self.lat.setValue(self.pending_coords[0]);self.lon.setValue(self.pending_coords[1])
        self.name.setFocus();self.name.selectAll()

    def station_changed(self,current,previous):
        if not current:self.station_id=None;return
        self.station_id=current.data(Qt.UserRole)
        row=next((s for s in self.db.stations(self.project_id) if s['id']==self.station_id),None)
        if row is None:return
        self.name.setText(row['name']);self.lat.setValue(row['latitude']);self.lon.setValue(row['longitude'])
        self.system.setCurrentText(row['system_type'])
        self.override.setChecked(row['frequency_override_mhz'] is not None)
        self.freq.setValue(self.db.effective_frequency(row))
        self.terrain_result=None
        self.terrain_source_label.setText('Quelle: '+(row['terrain_source'] or 'manuell / unbekannt')+'; Höhenbezug: '+(row['terrain_vertical_datum'] or 'nicht angegeben'))
        self.terrain_known.setChecked(row['terrain_height_m'] is not None)
        self.terrain.setValue(row['terrain_height_m'] if row['terrain_height_m'] is not None else 0)
        self.power.setValue(row['power_w'])
        self.height.setValue(row['antenna_height_m']);self.loss.setValue(row['cable_loss_db'])
        self.antenna.setCurrentText(row['antenna_name']);self.notes.setPlainText(row['notes'])

    def choose_terrain_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'GeoTIFF-Geländedatenordner auswählen')
        if folder:
            self.terrain_folder=folder
            self.terrain_folder_label.setText(folder)
            self.settings.setValue('terrain/library_path',folder)

    def open_terrain_library(self):
        """Shared terrain folder; import or download user-specified GeoTIFFs."""
        from .terrain_library import dataset_files, import_geotiffs, download_geotiff
        dlg=QDialog(self)
        dlg.setWindowTitle('RadioPlanner – Gemeinsame Geländedatenverwaltung (Beta)')
        dlg.resize(850,500)
        layout=QVBoxLayout(dlg)
        note=QLabel('Gemeinsamer Offline-Datenbestand für Sirenen, DMR, Richtfunk und später POCSAG.\n'
                    'Import und direkter HTTPS-GeoTIFF-Download. Automatische Gebietsauswahl und amtliche DGM-Kataloge folgen.\n'
                    'Höhenbezug, Lizenz und Abdeckung der Dateien bitte vor der Verwendung prüfen.')
        note.setWordWrap(True);layout.addWidget(note)
        path=QLabel(self.terrain_folder);path.setWordWrap(True);layout.addWidget(path)
        files=QListWidget();layout.addWidget(files)
        info=QLabel('');info.setWordWrap(True);layout.addWidget(info)
        def refresh():
            files.clear()
            datasets=dataset_files(self.terrain_folder)
            for item in datasets:
                files.addItem(f'{item.relative_to(self.terrain_folder)}  ({item.stat().st_size/1048576:.1f} MiB)')
            info.setText(f'{len(datasets)} GeoTIFF-Datei(en), {sum(x.stat().st_size for x in datasets)/1073741824:.2f} GiB.\n'
                         'Diese Dateien stehen für die vorhandene Offline-Geländeabfrage bereit.')
            path.setText(self.terrain_folder)
            self.terrain_folder_label.setText(self.terrain_folder)
        def choose():
            folder=QFileDialog.getExistingDirectory(dlg,'Gemeinsamen Geländedatenordner auswählen',self.terrain_folder)
            if folder:
                self.terrain_folder=folder
                self.settings.setValue('terrain/library_path',folder)
                refresh()
        def import_selected(paths, base_folder=None):
            if not paths:
                return
            progress=QProgressDialog('Geländedaten werden importiert ...','Abbrechen',0,len(paths),dlg)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            def task(emit,cancel):
                return import_geotiffs(paths,self.terrain_folder,base_folder=base_folder,
                    progress=lambda done,total: emit(f'{done}/{total}'),cancelled=cancel.is_set)
            worker=CalculationWorker(task,dlg)
            def update(message):
                done,total=map(int,message.split('/'))
                progress.setMaximum(total)
                progress.setValue(done)
                progress.setLabelText(f'Geländedaten importieren: {done} von {total}')
            def complete(result):
                progress.close()
                refresh()
                QMessageBox.information(dlg,'Import abgeschlossen',
                    f"{result['imported']} Datei(en) importiert, {result['skipped']} übersprungen.")
            worker.progress.connect(update)
            worker.completed.connect(complete)
            worker.failed.connect(lambda msg: (progress.close(),refresh(),self.alert(msg)))
            progress.canceled.connect(worker.cancel_event.set)
            worker.finished.connect(worker.deleteLater)
            worker.start()
            if not hasattr(dlg,'_terrain_workers'):dlg._terrain_workers=[]
            dlg._terrain_workers.append(worker)
        def import_file():
            names,_=QFileDialog.getOpenFileNames(dlg,'GeoTIFF-Dateien importieren','','GeoTIFF (*.tif *.tiff)')
            import_selected(names)
        def import_folder():
            folder=QFileDialog.getExistingDirectory(dlg,'DGM-Quellordner auswählen (inkl. Unterordner)')
            if not folder:
                return
            paths=dataset_files(folder)
            if not paths:
                self.alert('Im ausgewählten Ordner wurden keine .tif/.tiff-Dateien gefunden.')
                return
            if QMessageBox.question(dlg,'Ordnerimport bestätigen',
                f'{len(paths)} GeoTIFF-Datei(en) gefunden (inklusive Unterordner).\n'
                'Dateien werden in die gemeinsame Geländebibliothek kopiert. Fortfahren?',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.Yes)!=QMessageBox.Yes:
                return
            import_selected(paths,base_folder=folder)
        def download():
            url,ok=QInputDialog.getText(dlg,'GeoTIFF herunterladen','Direkte HTTPS-Adresse einer .tif/.tiff-Datei:')
            if not ok or not url.strip():return
            if QMessageBox.question(dlg,'Download bestätigen',
                'Bitte sicherstellen, dass der Anbieter den Download erlaubt und der Höhenbezug bekannt ist.\nJetzt herunterladen?',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
            progress=QProgressDialog('GeoTIFF wird heruntergeladen ...','Abbrechen',0,100,dlg)
            progress.setWindowModality(Qt.WindowModal);progress.setMinimumDuration(0)
            def task(emit,cancel):
                return download_geotiff(url.strip(),self.terrain_folder,
                    progress=lambda got,total: emit(f'{got/1048576:.1f} MiB'+(f' / {total/1048576:.1f} MiB' if total else '')),
                    cancelled=cancel.is_set)
            worker=CalculationWorker(task,dlg)
            worker.progress.connect(lambda msg: progress.setLabelText(msg))
            worker.completed.connect(lambda result: (progress.close(),refresh(),QMessageBox.information(dlg,'Download fertig',str(result))))
            worker.failed.connect(lambda msg: (progress.close(),self.alert(msg)))
            progress.canceled.connect(worker.cancel_event.set)
            worker.finished.connect(worker.deleteLater)
            worker.start()
            # Keep worker alive for the entire dialog lifetime.
            if not hasattr(dlg,'_terrain_workers'):dlg._terrain_workers=[]
            dlg._terrain_workers.append(worker)
        buttons=QHBoxLayout()
        for label,action in [('Ordner wählen',choose),('Dateien importieren',import_file),('Ordner importieren',import_folder),('GeoTIFF per HTTPS laden',download),('Aktualisieren',refresh),('Schließen',dlg.accept)]:
            button=QPushButton(label);button.clicked.connect(action);buttons.addWidget(button)
        layout.addLayout(buttons)
        refresh();dlg.exec()

    def query_terrain(self):
        if not self.terrain_folder:
            self.alert('Bitte zuerst einen lokalen DGM-Ordner mit GeoTIFF-Dateien auswählen.')
            return
        try:
            result=sample_folder(self.terrain_folder,self.lat.value(),self.lon.value())
            self.terrain_result=result
            self.terrain.setValue(result['height'])
            self.terrain_known.setChecked(True)
            self.terrain_source_label.setText('Quelle: '+result['source']+'; Höhenbezug: '+result['vertical_datum'])
            self.statusBar().showMessage('DGM-Höhe eingelesen. Zum Übernehmen bitte Sender speichern.')
        except TerrainError as exc:
            self.alert(exc)

    def query_online_terrain(self):
        try:
            result=sample_online(self.lat.value(),self.lon.value(),self.online_cache)
            self.terrain_result=result
            self.terrain.setValue(result['height'])
            self.terrain_known.setChecked(True)
            self.terrain_source_label.setText('Quelle: '+result['source']+'; Höhenbezug: '+result['vertical_datum'])
            self.statusBar().showMessage('Online-Höhe ermittelt. Zum Übernehmen Sender speichern. Kein amtliches DGM1.')
        except TerrainError as exc:
            self.alert(exc)

    def toggle_probe_mode(self):
        if not self.probe_data:
            self.alert('Bitte zuerst eine Schallpegelwolke berechnen.')
            return
        self.probe_mode=not self.probe_mode
        self.map.set_probe_mode(self.probe_mode)
        self.map.set_hint('Schallpegelabfrage: Punkt auf der Karte anklicken' if self.probe_mode else 'Klick auf die Karte: neuen Sender anlegen')

    def probe_clicked(self,lat,lon):
        if not self.probe_data:return
        from .acoustic_grid import level_at
        data=self.probe_data
        result=level_at(data['sirens'],lat,lon,data['reference_db'],data['attenuation'],data['terrain_screen'],data.get('building_screen'))
        lines=[f'Koordinaten: {lat:.6f}° N, {lon:.6f}° E',
               f'Gesamtpegel: {result["level_db"]:.1f} dB (Modellwert)',
               f'Kartenschwelle: {data["threshold"]:.1f} dB', '', 'Beiträge:']
        for c in sorted(result['contributions'],key=lambda x:-x['level_db']):
            lines.append(f'{c["name"]}: {c["level_db"]:.1f} dB, Entfernung {c["distance_m"]:.0f} m')
        lines.extend(['','Vereinfachte Modellrechnung, keine validierte Schallprognose.'])
        QMessageBox.information(self,'Schallpegel am Kartenpunkt','\n'.join(lines))

    def start_area_polygon(self):
        if self.project_id is None:self.alert('Bitte zuerst ein Projekt wählen.');return
        self.map_mode='polygon';self.analysis_polygon=None
        self.map.start_polygon()

    def map_finish_polygon(self):
        self.map.finish_polygon()

    def polygon_received(self, payload):
        try:
            polygon=json.loads(payload)
            if len(polygon)<3 or any(len(pt)!=2 or not (-85<float(pt[0])<85 and -180<=float(pt[1])<=180) for pt in polygon):
                raise ValueError('Ungültiges Polygon.')
            self.analysis_polygon=[[float(a),float(b)] for a,b in polygon]
            self.map_mode='idle'
            self.map.set_hint('Polygon übernommen – Versorgungslücken und Flächenanalyse öffnen')
            self.statusBar().showMessage(f'Untersuchungsgebiet: {len(polygon)} Eckpunkte übernommen.')
        except (ValueError,TypeError) as exc:self.alert(exc)

    def start_siren_simulation(self):
        if self.project_id is None or not list(self.db.sirens(self.project_id)):
            self.alert('Bitte zuerst ein Projekt mit mindestens einer Sirene öffnen.');return
        self.map_mode='simulate_siren'
        self.map.set_hint('Auf die Karte klicken, um eine zusätzliche Sirene zu simulieren')

    def discard_simulation(self):
        self.simulated_siren=None
        self.map.clear_simulation()
        self.statusBar().showMessage('Simulation verworfen. Keine Standorte wurden verändert.')

    def compare_simulation(self):
        if not self.simulated_siren:
            self.alert('Bitte zuerst einen simulierten Standort auf der Karte setzen.');return
        self.show_coverage_gaps(compare=True)

    def cancel_dmr_map_pick(self):
        if self.map_mode!='dmr_pick':return
        pick=self._dmr_map_pick
        self._dmr_map_pick=None;self.map_mode='idle'
        self.map.set_hint('DMR-Geräte über DMR Tier II auf der Karte platzieren')
        if pick:
            dlg=pick[0];dlg.show();dlg.raise_();dlg.activateWindow()
        self.statusBar().showMessage('DMR-Kartenauswahl abgebrochen.',5000)

    def map_clicked(self,lat,lon):
        if self.map_mode=='dmr_pick':
            pick=self._dmr_map_pick
            self._dmr_map_pick=None;self.map_mode='idle'
            self.map.set_hint('DMR-Geräte über DMR Tier II auf der Karte platzieren')
            if pick:
                dlg,field_lat,field_lon,on_position=pick
                field_lat.setValue(lat);field_lon.setValue(lon)
                if on_position is not None:
                    on_position()
                dlg.show();dlg.raise_();dlg.activateWindow()
                self.statusBar().showMessage('DMR-Position übernommen'+(' und Gerät gespeichert.' if on_position is not None else '. Bitte Repeater speichern.'),7000)
            return
        # Fallback: falls der JS-Zeichenmodus nicht aktiv ist, niemals Sender anlegen.
        if self.map_mode == 'polygon':
            self.map.add_polygon_point(lat,lon)
            return
        if self.map_mode=='simulate_siren':
            self.map_mode='idle'
            self.map.set_hint('DMR-Geräte über DMR Tier II auf der Karte platzieren')
            records=[dict(r) for r in self.db.sirens(self.project_id)]
            names=[r['name'] for r in records]
            name,ok=QInputDialog.getItem(self,'Simulation','Vorhandene Sirene als akustische Vorlage:',names,0,False)
            if not ok:return
            template=dict(records[names.index(name)])
            template['latitude']=float(lat);template['longitude']=float(lon)
            template['name']='Simulation: '+name
            # Die Standort-ID darf niemals als realer Standort interpretiert werden.
            template['id']=None
            self.simulated_siren=template
            self.map.show_simulation(lat,lon)
            self.statusBar().showMessage('Simulierte Sirene gesetzt (nicht gespeichert). Jetzt „Simulation vergleichen“ wählen.')
            return
        if self.map_mode == 'sirene':
            self.map_mode='idle'
            self.map.set_hint('DMR-Geräte über DMR Tier II auf der Karte platzieren')
            self.open_siren_dialog(coords=(lat,lon))
        elif self.map_mode == 'move_siren':
            sid=self.move_siren_id
            self.map_mode='idle';self.move_siren_id=None
            self.map.set_hint('DMR-Geräte über DMR Tier II auf der Karte platzieren')
            self.relocate_siren(sid,lat,lon)
        else:
            self.statusBar().showMessage('Geräte über DMR Tier II oder Sirenenplanung platzieren.', 4000)

    def select_station(self,station_id):
        for i in range(self.stations.count()):
            item=self.stations.item(i)
            if item.data(Qt.UserRole)==station_id:self.stations.setCurrentItem(item);break

    def save_station(self):
        if self.project_id is None:self.alert('Bitte zuerst ein Projekt anlegen.');return
        if not self.terrain_known.isChecked() and self.terrain_folder:
            try:
                self.query_terrain()
            except Exception as exc:
                self.alert(exc)
                return
        previous=next((r for r in self.db.stations(self.project_id) if r['id']==self.station_id),None)
        values={'name':self.name.text(),'latitude':self.lat.value(),'longitude':self.lon.value(),
          'frequency_mhz':self.freq.value() if self.override.isChecked() else self.db.project(self.project_id)['network_frequency_mhz'],'power_w':self.power.value(),'antenna_height_m':self.height.value(),
          'antenna_name':self.antenna.currentText(),'cable_loss_db':self.loss.value(),'notes':self.notes.toPlainText()}
        try:
            sid=self.db.save_station(self.project_id,values,self.station_id)
            self.db.set_station_details(sid,self.project_id,self.system.currentText(),
                self.terrain.value() if self.terrain_known.isChecked() else None,
                self.freq.value() if self.override.isChecked() else None)
            if self.terrain_known.isChecked() and self.terrain_result is not None and abs(self.terrain.value()-self.terrain_result['height']) < 0.001:
                result=self.terrain_result
                self.db.set_terrain_result(sid,self.project_id,result['height'],result['source'],result['vertical_datum'],result['resolution'])
            elif previous is None or not self.terrain_known.isChecked() or previous['terrain_height_m'] != self.terrain.value() or previous['latitude'] != self.lat.value() or previous['longitude'] != self.lon.value():
                self.db.conn.execute('UPDATE stations SET terrain_source=NULL, terrain_vertical_datum=NULL, terrain_resolution_m=NULL WHERE id=? AND project_id=?',(sid,self.project_id))
                self.db.conn.commit()
            self.reload_stations(sid);self.reload_projects(self.project_id)
        except (ValueError,Exception) as e:self.alert(e)

    def delete_station(self):
        if self.project_id is None or self.station_id is None:return
        if QMessageBox.question(self,'Sender löschen','Senderstandort löschen?')==QMessageBox.Yes:
            self.db.delete_station(self.project_id,self.station_id)
            self.reload_stations();self.reload_links();self.reload_projects(self.project_id)

    def update_frequency_control(self):
        self.freq.setEnabled(self.override.isChecked())
        if not self.override.isChecked() and self.project_id is not None:
            self.freq.setValue(self.db.project(self.project_id)['network_frequency_mhz'])

    def update_height(self, *args):
        self.terrain.setEnabled(self.terrain_known.isChecked())
        if self.terrain_known.isChecked():
            self.absolute.setText(f'{self.terrain.value()+self.height.value():.1f} m (Höhenbezug des DGM / manuell)')
        else:
            self.absolute.setText('Geländehöhe noch unbekannt')

    def save_network_frequency(self):
        if self.project_id is None:return
        try:
            self.db.set_project_frequency(self.project_id,self.network_freq.value())
            self.reload_stations(self.station_id)
            self.update_frequency_control()
        except ValueError as e:self.alert(e)

    def reload_links(self):
        self.links.clear()
        if self.project_id is None:
            self.map.set_radio_links([])
            return
        station_rows={s['id']:s for s in self.db.stations(self.project_id)}
        names={sid:st['name'] for sid,st in station_rows.items()}
        map_links=[]
        for link in self.db.radio_links(self.project_id):
            sa=station_rows.get(link['station_a_id']);sb=station_rows.get(link['station_b_id'])
            distance=None
            if sa and sb:
                la1,lo1,la2,lo2=map(math.radians,(sa['latitude'],sa['longitude'],sb['latitude'],sb['longitude']))
                h=math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
                distance=6371.0088*2*math.asin(min(1,math.sqrt(h)))
            status='unknown';margin=None
            if distance is not None and distance>0:
                try:
                    result=calculate_link_budget(distance,link['frequency_ghz']*1000,
                        link['tx_power_dbm'],link['gain_a_dbi'],link['gain_b_dbi'],
                        link['loss_a_db'],link['loss_b_db'],link['rx_sensitivity_dbm'])
                    margin=result['fade_margin_db'];status=status_for_margin(margin,link['reserve_target_db'])
                except (ValueError,TypeError,KeyError):
                    pass
            overall, reason=assess_overall(status,link['terrain_status'],link['terrain_source'])
            status=overall
            style={'green':('🟢','#188038','Zielreserve erreicht'),
                   'yellow':('🟡','#a86b00','Geringe Reserve'),
                   'red':('🔴','#c62828','Rechnerisch nicht ausreichend'),
                   'unknown':('⚪','#697386','Geländeprüfung fehlt')}[status]
            label=f"{style[0]} {link['name']}: {names.get(link['station_a_id'],'?')} ↔ {names.get(link['station_b_id'],'?')} ({link['frequency_ghz']:g} GHz"
            if distance is not None:label+=f' · {distance:.2f} km'
            if margin is not None:label+=f' · {margin:+.1f} dB'
            label+=f' · Gesamt: {style[2]}'
            item=QListWidgetItem(label)
            item.setForeground(QColor(style[1]))
            item.setToolTip(reason+f"; Zielreserve {link['reserve_target_db']:g} dB. Vereinfachtes Modell.")
            item.setData(Qt.UserRole,link['id']);self.links.addItem(item)
            if sa and sb:
                map_links.append({'a':[sa['latitude'],sa['longitude']],
                    'b':[sb['latitude'],sb['longitude']], 'name':link['name'],
                    'status':status,'margin':margin})
        self.map.set_radio_links(map_links)

    def add_link(self):
        self.open_link_dialog()

    def edit_link(self):
        item=self.links.currentItem()
        if item is not None:
            self.open_link_dialog(item.data(Qt.UserRole))

    def open_link_dialog(self, link_id=None):
        if self.project_id is None:return self.alert('Bitte zuerst ein Projekt wählen.')
        stations=self.db.stations(self.project_id)
        if len(stations)<2:return self.alert('Für Richtfunk werden mindestens zwei Standorte benötigt.')
        record=self.db.radio_link(self.project_id,link_id) if link_id is not None else None
        if link_id is not None and record is None:return self.alert('Richtfunkstrecke nicht gefunden.')
        dialog=QDialog(self)
        dialog.setWindowTitle('Richtfunkstrecke bearbeiten' if record else 'Richtfunkstrecke anlegen')
        dialog.resize(980,720)
        dialog.setMinimumSize(620,420)
        dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        outer=QVBoxLayout(dialog)
        tabs=QTabWidget(dialog)
        outer.addWidget(tabs)
        # Initial form is populated as before, then distributed into independent tabs.
        content=QWidget()
        form=QFormLayout(content)
        name=QLineEdit(record['name'] if record else 'Neue Richtfunkstrecke')
        form.addRow('Name',name)
        a=QComboBox();b=QComboBox()
        for station in stations:
            a.addItem(station['name'],station['id']);b.addItem(station['name'],station['id'])
        def select_station(combo, station_id):
            for index in range(combo.count()):
                if combo.itemData(index)==station_id:combo.setCurrentIndex(index);break
        if record:
            select_station(a,record['station_a_id']);select_station(b,record['station_b_id'])
        else:b.setCurrentIndex(1)
        form.addRow('Standort A',a);form.addRow('Standort B',b)
        kind=QComboBox();kind.addItems(['5 GHz','Lizenziert'])
        kind.setCurrentText(record['link_type'] if record else '5 GHz')
        form.addRow('Frequenzzuteilung',kind)
        regulatory=QLabel('5 GHz: Allgemeinzuteilung nur bei Einhaltung der jeweils gültigen Vorgaben.\nLizenziert: individuelle Frequenzzuteilung prüfen / beantragen.')
        regulatory.setWordWrap(True);form.addRow(regulatory)
        freq=self.spin(100,100000,3,record['frequency_ghz']*1000 if record else 5800)
        form.addRow('Frequenz (MHz)',freq)
        def change_kind(text):
            if record is None:freq.setValue(5800 if text=='5 GHz' else 18000)
        kind.currentTextChanged.connect(change_kind)
        def field(label,key,minimum,maximum,decimals,default):
            value=record[key] if record else default
            widget=self.spin(minimum,maximum,decimals,value)
            form.addRow(label,widget)
            return widget
        bandwidth=field('Kanalbreite (MHz)','bandwidth_mhz',0.01,2000,2,20)
        tx=field('Sendeleistung (dBm)','tx_power_dbm',-100,100,2,20)
        rx=field('Empfindlichkeit (dBm)','rx_sensitivity_dbm',-160,0,2,-75)
        ga=field('Antennengewinn A (dBi)','gain_a_dbi',-30,80,2,27)
        gb=field('Antennengewinn B (dBi)','gain_b_dbi',-30,80,2,27)
        ha=field('Antennenhöhe A über Grund (m)','height_a_m',0,10000,2,25)
        hb=field('Antennenhöhe B über Grund (m)','height_b_m',0,10000,2,30)
        la=field('Systemverluste A (dB)','loss_a_db',0,100,2,1)
        lb=field('Systemverluste B (dB)','loss_b_db',0,100,2,1)
        library=EquipmentLibrary()
        antenna_a=QComboBox();antenna_b=QComboBox();radio=QComboBox()
        for combo,cat in ((antenna_a,'Antenne'),(antenna_b,'Antenne'),(radio,'Funkgerät')):
            combo.addItem('Manuelle Eingabe / keine Zuordnung',None)
            for item in library.list(cat):
                combo.addItem(item['manufacturer']+' – '+item['model']+(' [Muster]' if item.get('example') else ''),item['id'])
        form.addRow('Antennenmodell A',antenna_a);form.addRow('Antennenmodell B',antenna_b)
        form.addRow('Funkgerät (beide Enden)',radio)
        def restore(combo,key):
            if record and key in record.keys():
                index=combo.findData(record[key]);combo.setCurrentIndex(max(0,index))
        restore(antenna_a,'antenna_a_model_id');restore(antenna_b,'antenna_b_model_id');restore(radio,'radio_model_id')
        def choose_antenna(combo,widget):
            item=library.get(combo.currentData())
            if item:
                widget.setValue(item['gain_dbi'])
                if not item['frequency_min_mhz']<=freq.value()<=item['frequency_max_mhz']:
                    QMessageBox.warning(dialog,'Frequenzbereich','Die gewählte Antenne ist nicht für diese Frequenz hinterlegt.')
        def choose_radio(*args):
            item=library.get(radio.currentData())
            if item:
                tx.setValue(item['tx_power_dbm']);rx.setValue(item['rx_sensitivity_dbm'])
                if not item['frequency_min_mhz']<=freq.value()<=item['frequency_max_mhz']:
                    QMessageBox.warning(dialog,'Frequenzbereich','Das gewählte Funkgerät ist nicht für diese Frequenz hinterlegt.')
        antenna_a.currentIndexChanged.connect(lambda _:choose_antenna(antenna_a,ga))
        antenna_b.currentIndexChanged.connect(lambda _:choose_antenna(antenna_b,gb))
        radio.currentIndexChanged.connect(choose_radio)
        form.addRow(QLabel('Musterwerte sind keine validierten Gerätedaten. Vor realer Planung Datenblatt prüfen.'))
        eirp=QLabel();form.addRow('EIRP Standort A',eirp)
        def update_eirp(*args):
            eirp.setText(f'{tx.value()+ga.value()-la.value():.2f} dBm (rechnerisch)')
        for w in (tx,ga,la):w.valueChanged.connect(update_eirp)
        update_eirp()
        budget_title=QLabel('LINK-BUDGET (Freiraummodell / Vorplanung)')
        form.addRow(budget_title)
        distance_label=QLabel('Bitte unterschiedliche Standorte wählen.')
        form.addRow('Streckenlänge',distance_label)
        budget_result=QLabel()
        budget_result.setWordWrap(True)
        budget_result.setTextFormat(Qt.PlainText)
        budget_status=QTextEdit('⚪ Nicht bewertet')
        budget_status.setReadOnly(True)
        budget_status.setAcceptRichText(False)
        budget_status.setMinimumHeight(100)
        budget_status.setMaximumHeight(180)
        form.addRow('Ampelbewertung',budget_status)
        form.addRow('Empfangsberechnung',budget_result)
        results_box=QGroupBox('Streckenergebnisse – aktuelle Eingaben')
        results_layout=QVBoxLayout(results_box)
        results_text=QLabel('Werte werden berechnet.')
        results_text.setTextFormat(Qt.PlainText);results_text.setWordWrap(True)
        results_layout.addWidget(results_text)
        form.addRow(results_box)

        reserve_target=self.spin(0,80,1,record['reserve_target_db'] if record else 20)
        form.addRow('Ziel-Fadingreserve (dB)',reserve_target)
        scenario_label=QLabel('3.0 – Dämpfungsszenario (manuelle Annahmen, keine Verfügbarkeitsprognose)')
        scenario_label.setWordWrap(True)
        form.addRow(scenario_label)
        rain_specific=self.spin(0,100,3,record['rain_specific_db_km'] if record else 0)
        gas_specific=self.spin(0,100,3,record['gas_specific_db_km'] if record else 0)
        wet_fraction=self.spin(0,1,2,record['wet_path_fraction'] if record else 1)
        form.addRow('Regen-Szenario: spezifische Dämpfung (dB/km)',rain_specific)
        form.addRow('Atmosphäre: spezifische Dämpfung (dB/km)',gas_specific)
        form.addRow('Anteil regenbeeinflusster Strecke (0–1)',wet_fraction)
        automatic_title=QLabel('3.0.4 – ITU-R-Modellrechnung und Regenverfügbarkeitsabschätzung')
        automatic_title.setWordWrap(True)
        form.addRow(automatic_title)
        rain_rate=self.spin(0,500,1,record['itu_rain_rate_mm_h'] if record else 25)
        form.addRow('Regenintensität für Szenario (mm/h)',rain_rate)
        pol=QComboBox();pol.addItems(['Horizontal','Vertikal'])
        if record: pol.setCurrentText(record['itu_polarization'])
        form.addRow('Polarisation für Regenmodell',pol)
        temp_c=self.spin(-50,60,1,record['itu_temperature_c'] if record else 15)
        form.addRow('Lufttemperatur (°C)',temp_c)
        pressure_hpa=self.spin(300,1100,1,record['itu_pressure_hpa'] if record else 1013)
        form.addRow('Luftdruck (hPa)',pressure_hpa)
        vapor=self.spin(0,60,1,record['itu_vapor_g_m3'] if record else 7.5)
        form.addRow('Wasserdampfdichte (g/m³)',vapor)
        rain_lookup=QPushButton('Regionale Regenrate R0,01 nach ITU-R P.837 übernehmen')
        rain_lookup.setToolTip('Ermittelt R0,01 aus den Standortkoordinaten. Benötigt das Paket itur.')
        form.addRow(rain_lookup)
        regional_status=QTextEdit('Noch keine regionale Regenrate abgerufen.')
        regional_status.setReadOnly(True)
        regional_status.setAcceptRichText(False)
        regional_status.setMinimumHeight(115)
        regional_status.setMaximumHeight(220)
        form.addRow('P.837-Abrufstatus',regional_status)
        def load_regional_rain():
            station_map={station['id']:station for station in stations}
            sa=station_map.get(a.currentData()); sb=station_map.get(b.currentData())
            if not sa or not sb or sa['id']==sb['id']:
                regional_status.setText('Fehler: Bitte zwei verschiedene Standorte auswählen.')
                QMessageBox.warning(dialog,'Regenstatistik',regional_status.toPlainText())
                return
            try:
                from .propagation import regional_rain_rate
                lat=(float(sa['latitude'])+float(sb['latitude']))/2
                lon=(float(sa['longitude'])+float(sb['longitude']))/2
                rate=regional_rain_rate(lat,lon)
                if not math.isfinite(rate) or rate<0 or rate>500:
                    raise ValueError('Ungültige Regenrate aus ITU-R P.837.')
                rain_rate.setValue(rate)
                displayed=rain_rate.value()
                message=(f'ITU-R P.837: R0,01 = {rate:.2f} mm/h '
                         f'(Eingabefeld: {displayed:.1f} mm/h). '
                         f'Streckenmittelpunkt: {lat:.5f}°, {lon:.5f}°. '
                         'Klimastatistik; keine jährliche Verfügbarkeit.')
                regional_status.setText(message)
                QMessageBox.information(dialog,'Regionale Regenrate übernommen',message)
            except Exception as exc:
                message=(f'Regionale Regenrate konnte nicht ermittelt werden: '
                         f'{type(exc).__name__}: {exc}. '
                         'Der bisherige Eingabewert wurde NICHT überschrieben. '
                         'Prüfe: py -3.12 -m pip show itur')
                regional_status.setText(message)
                QMessageBox.warning(dialog,'P.837-Abruf fehlgeschlagen',message)
        rain_lookup.clicked.connect(load_regional_rain)
        automatic_result=QLabel('')
        automatic_result.setTextFormat(Qt.PlainText)
        automatic_result.setWordWrap(True)
        form.addRow('ITU-R-Szenario',automatic_result)
        availability_result=QTextEdit('Noch keine Regenverfügbarkeitsabschätzung.')
        availability_result.setReadOnly(True)
        availability_result.setAcceptRichText(False)
        availability_result.setMinimumHeight(115)
        availability_result.setMaximumHeight(220)
        form.addRow('P.530 Regenverfügbarkeit', availability_result)
        availability_button=QPushButton('Regenverfügbarkeit berechnen (ITU-R P.530)')
        form.addRow(availability_button)
        scenario_result=QTextEdit('')
        scenario_result.setReadOnly(True)
        scenario_result.setAcceptRichText(False)
        scenario_result.setMinimumHeight(115)
        scenario_result.setMaximumHeight(220)
        form.addRow('Szenarioergebnis',scenario_result)
        def update_budget(*args):
            station_map={station['id']:station for station in stations}
            sa=station_map.get(a.currentData());sb=station_map.get(b.currentData())
            if not sa or not sb or sa['id']==sb['id']:
                distance_label.setText('Zwei verschiedene Standorte erforderlich')
                budget_result.setText('Keine Berechnung möglich.')
                scenario_result.setText('Keine Berechnung möglich.')
                automatic_result.setText('Keine Berechnung möglich.')
                availability_result.setText('Keine Berechnung möglich.')
                results_text.setText('Zwei unterschiedliche Standorte auswählen.')
                budget_status.setText('⚪ Nicht bewertet')
                budget_status.setStyleSheet('')
                return
            lat1,lon1,lat2,lon2=map(math.radians,(sa['latitude'],sa['longitude'],sb['latitude'],sb['longitude']))
            delta=2*math.asin(min(1,math.sqrt(math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2)))
            distance=6371.0088*delta
            distance_label.setText(f'{distance:.3f} km (Luftlinie)')
            from .profile import antenna_alignment
            alignment=antenna_alignment((sa['latitude'],sa['longitude']),(sb['latitude'],sb['longitude']),ha.value(),hb.value())
            try:
                result=calculate_link_budget(distance,freq.value(),tx.value(),ga.value(),gb.value(),la.value(),lb.value(),rx.value(),reserve_target.value())
                budget_result.setText(f"Freiraumdämpfung: {result['fspl_db']:.2f} dB\n"
                                      f"EIRP A: {result['eirp_dbm']:.2f} dBm\n"
                                      f"Empfangspegel B: {result['received_dbm']:.2f} dBm\n"
                                      f"Fadingreserve: {result['fade_margin_db']:.2f} dB\n"
                                      f"Bewertung: {result['assessment']}")
                from .propagation import scenario_losses
                losses=scenario_losses(distance,freq.value(),rain_specific.value(),gas_specific.value(),wet_fraction.value())
                scenario_rx=result['received_dbm']-losses['additional_db']
                scenario_margin=result['fade_margin_db']-losses['additional_db']
                scenario_result.setText(
                    f"Regen: {losses['rain_db']:.2f} dB | Atmosphäre: {losses['atmosphere_db']:.2f} dB\n"
                    f"Zusatzdämpfung: {losses['additional_db']:.2f} dB | Empfangspegel: {scenario_rx:.2f} dBm\n"
                    f"Verbleibende Reserve: {scenario_margin:+.2f} dB\n"
                    'Keine jährliche Verfügbarkeit berechnet. Werte sind manuelle Szenarioannahmen, keine ITU-R-Modellwerte.')
                gas_loss=None
                try:
                    from .propagation import itu_specific_losses
                    auto=itu_specific_losses(freq.value(),rain_rate.value(),pol.currentText(),
                        temp_c.value(),pressure_hpa.value(),vapor.value())
                    rain_loss=auto['rain_specific_db_km']*distance*wet_fraction.value()
                    gas_loss=auto['gas_specific_db_km']*distance
                    automatic_result.setText(
                        f"P.838 Regen: {auto['rain_specific_db_km']:.3f} dB/km → {rain_loss:.2f} dB\n"
                        f"P.676 Atmosphäre: {auto['gas_specific_db_km']:.3f} dB/km → {gas_loss:.2f} dB\n"
                        f"Gesamt: {rain_loss+gas_loss:.2f} dB | Restreserve: {result['fade_margin_db']-rain_loss-gas_loss:+.2f} dB\n"
                        'Vorgegebene Regenrate; keine regionale Regenstatistik oder Jahresverfügbarkeit.')
                except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
                    automatic_result.setText('ITU-R-Berechnung nicht verfügbar: '+str(exc))
                terrain_note='Gelände/Fresnel: nicht für diese Eingaben geprüft' 
                if record and a.currentData()==record['station_a_id'] and b.currentData()==record['station_b_id'] and abs(freq.value()-record['frequency_ghz']*1000)<0.01 and abs(ha.value()-record['height_a_m'])<0.01 and abs(hb.value()-record['height_b_m'])<0.01:
                    terrain_note='Gelände/Fresnel: '+str(record['terrain_status'] or 'nicht geprüft')+' (gespeicherte Prüfung; Standortänderungen beachten)'
                results_text.setText(f"Strecke: {distance:.3f} km | Frequenz: {freq.value():.3f} MHz\n"
                    f"Antennenhöhen A/B: {ha.value():.2f} / {hb.value():.2f} m über Grund\n"
                    f"Sendeleistung: {tx.value():.2f} dBm | Antennengewinne A/B: {ga.value():.2f} / {gb.value():.2f} dBi\n"
                    f"Systemverluste A/B: {la.value():.2f} / {lb.value():.2f} dB\n"
                    f"EIRP A: {result['eirp_dbm']:.2f} dBm | Freiraumdämpfung: {result['fspl_db']:.2f} dB\n"
                    f"Empfangspegel B: {result['received_dbm']:.2f} dBm | Empfängerempfindlichkeit: {rx.value():.2f} dBm\n"
                    f"Fadingreserve: {result['fade_margin_db']:+.2f} dB | Ziel: {reserve_target.value():.1f} dB\n"
                    f"Azimut A → B: {alignment['azimuth_a_deg']:.2f}° | Azimut B → A: {alignment['azimuth_b_deg']:.2f}° (von Nord, im Uhrzeigersinn)\n"
                    "Höhenwinkel: erst nach Geländeprofil mit Geländehöhen bestimmbar\n"
                    +terrain_note)
                status=status_for_margin(result['fade_margin_db'],reserve_target.value())
                color,icon={'green':('#188038','●'),'yellow':('#9a6700','▲'),'red':('#c62828','✖')}[status]
                budget_status.setText(f"{icon} {result['assessment']} ({result['fade_margin_db']:+.2f} dB)")
                budget_status.setStyleSheet(f'color: {color}; font-weight: bold; padding: 5px;')
            except ValueError as exc:
                budget_result.setText(str(exc))
                scenario_result.setText(str(exc))
                automatic_result.setText(str(exc))
                availability_result.setText(str(exc))
                results_text.setText(str(exc))
                budget_status.setText('⚪ Nicht bewertet')
                budget_status.setStyleSheet('')
        pending_p530 = {"value": None}
        def calculate_rain_availability():
            """Run P.530 explicitly and show errors rather than a stale placeholder."""
            availability_result.setText('P.530-Berechnung läuft …')
            station_map={station['id']:station for station in stations}
            sa=station_map.get(a.currentData()); sb=station_map.get(b.currentData())
            if not sa or not sb or sa['id']==sb['id']:
                availability_result.setText('Fehler: Bitte zwei verschiedene Standorte auswählen.')
                return
            try:
                lat1,lon1,lat2,lon2=map(math.radians,(sa['latitude'],sa['longitude'],sb['latitude'],sb['longitude']))
                delta=2*math.asin(min(1,math.sqrt(math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2)))
                distance=6371.0088*delta
                link=calculate_link_budget(distance,freq.value(),tx.value(),ga.value(),gb.value(),la.value(),lb.value(),rx.value(),reserve_target.value())
                from .propagation import itu_specific_losses, rain_availability_estimate
                auto=itu_specific_losses(freq.value(),rain_rate.value(),pol.currentText(),temp_c.value(),pressure_hpa.value(),vapor.value())
                gas_loss=auto['gas_specific_db_km']*distance
                estimate=rain_availability_estimate((float(sa['latitude'])+float(sb['latitude']))/2,
                    (float(sa['longitude'])+float(sb['longitude']))/2,distance,freq.value(),
                    pol.currentText(),link['fade_margin_db'],gas_loss)
                if estimate['status']=='insufficient':
                    message=(f"R0,01: {estimate['r001_mm_h']:.2f} mm/h | "
                             f"Fadingreserve: {link['fade_margin_db']:.2f} dB | Gasdämpfung: {gas_loss:.2f} dB\n"
                             +estimate['note'])
                else:
                    label={'estimate':'Schätzwert','upper_bound':'Obere Grenze der Ausfallwahrscheinlichkeit',
                           'lower_bound':'Untere Grenze der Ausfallwahrscheinlichkeit'}.get(estimate['status'],'Modellgrenze')
                    comparator={'estimate':'≈','upper_bound':'<','lower_bound':'>'}.get(estimate['status'],'≈')
                    message=(f"{label} (nur Regen):\n"
                             f"Ausfallanteil: {comparator} {estimate['p_percent']:.4f} % des Jahres\n"
                             f"Ausfallzeit: {comparator} {estimate['outage_hours_year']:.2f} Stunden/Jahr\n"
                             f"Regenbedingte Verfügbarkeit: {('≈' if estimate['status']=='estimate' else 'Grenzwert ')}{estimate['availability_percent']:.4f} %\n"
                             f"R0,01: {estimate['r001_mm_h']:.2f} mm/h | Reserve: {link['fade_margin_db']:.2f} dB | Gas: {gas_loss:.2f} dB\n"
                             +estimate['note'])
                availability_result.setText(message)
                pending_p530['value']={'estimate':estimate,'fade_margin_db':link['fade_margin_db'],'gas_loss_db':gas_loss}
                # Existing, unchanged records can be cached immediately. Edited
                # dialog values are committed only after OK saves the link.
                if record is not None:
                    from .analysis_cache import signature
                    current=dict(record)
                    current.update(station_a_id=a.currentData(),station_b_id=b.currentData(),
                        frequency_ghz=freq.value()/1000,tx_power_dbm=tx.value(),
                        rx_sensitivity_dbm=rx.value(),gain_a_dbi=ga.value(),gain_b_dbi=gb.value(),
                        loss_a_db=la.value(),loss_b_db=lb.value(),reserve_target_db=reserve_target.value(),
                        height_a_m=ha.value(),height_b_m=hb.value(),
                        itu_rain_rate_mm_h=rain_rate.value(),itu_polarization=pol.currentText(),
                        itu_temperature_c=temp_c.value(),itu_pressure_hpa=pressure_hpa.value(),itu_vapor_g_m3=vapor.value())
                    if signature(current,station_map)==signature(record,station_map):
                        existing=self._report_analyses([record]).get(record['id']) or {}
                        existing['p530']=pending_p530['value']
                        self._save_report_analysis(record,existing)
            except Exception as exc:
                availability_result.setText(f'P.530-Berechnung fehlgeschlagen: {type(exc).__name__}: {exc}')
        availability_button.clicked.connect(calculate_rain_availability)
        def invalidate_rain_availability(*args):
            pending_p530['value']=None
            availability_result.setText('Eingaben geändert. Bitte „Regenverfügbarkeit berechnen“ anklicken.')
        for widget in (a,b,):widget.currentIndexChanged.connect(update_budget)
        for widget in (freq,tx,rx,ga,gb,la,lb,reserve_target,rain_specific,gas_specific,wet_fraction,rain_rate,temp_c,pressure_hpa,vapor):widget.valueChanged.connect(update_budget)
        pol.currentIndexChanged.connect(update_budget)
        for widget in (a,b,pol): widget.currentIndexChanged.connect(invalidate_rain_availability)
        for widget in (freq,tx,rx,ga,gb,la,lb,reserve_target,rain_rate,temp_c,pressure_hpa,vapor):
            widget.valueChanged.connect(invalidate_rain_availability)
        update_budget()
        note=QLabel('Vereinfachte Freiraumberechnung. Keine Prüfung von Interferenzen, Mehrwegedämpfung, statistischer Regenwahrscheinlichkeit, Verfügbarkeit oder regulatorischen Grenzwerten. Eine freie Fresnelzone muss separat geprüft werden.')
        note.setWordWrap(True)
        form.addRow(note)
        info=QLabel('Noch keine Geländeprüfung oder regulatorische Zulassungsprüfung.')
        info.setWordWrap(True);form.addRow(info)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('OK')
        buttons.button(QDialogButtonBox.Cancel).setText('Abbrechen')
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        # Separate the previously overlong form into scrollable, maximizable pages.
        # Move existing widgets instead of rebuilding them, preserving all signals.
        page_names=['Standorte & Funkparameter','Geräte & Antennen',
                    'Link-Budget & Ergebnisse','Dämpfungsszenario',
                    'ITU-R & Verfügbarkeit','Einzelbericht']
        page_forms=[]
        for page_name in page_names:
            page_scroll=QScrollArea(tabs)
            page_scroll.setWidgetResizable(True)
            page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            page_widget=QWidget()
            page_form=QFormLayout(page_widget)
            page_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            page_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            page_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            page_form.setSpacing(10)
            page_form.setContentsMargins(18,18,18,18)
            page_scroll.setWidget(page_widget)
            tabs.addTab(page_scroll,page_name)
            page_forms.append(page_form)
        page=0
        while form.rowCount():
            row=form.takeRow(0)
            label_item=row.labelItem
            field_item=row.fieldItem
            label_widget=label_item.widget() if label_item else None
            field_widget=field_item.widget() if field_item else None
            label_text=label_widget.text() if isinstance(label_widget,QLabel) else ''
            if field_widget is antenna_a or label_text=='Antennenmodell A': page=1
            elif field_widget is budget_title or label_text.startswith('LINK-BUDGET'): page=2
            elif field_widget is scenario_label or label_text.startswith('3.0 – Dämpfungsszenario'): page=3
            elif field_widget is automatic_title or label_text.startswith('3.0.4 – ITU-R'): page=4
            if label_widget and field_widget:
                page_forms[page].addRow(label_widget,field_widget)
            elif field_widget:
                page_forms[page].addRow(field_widget)
            elif label_widget:
                page_forms[page].addRow(label_widget)
        tabs.setCurrentIndex(0)
        report_page=page_forms[5]
        report_info=QLabel('PDF-Bericht für diese Richtfunkstrecke mit Link-Budget und, sofern zuvor berechnet, dem echten Geländeprofil mit Sichtlinie und Fresnelzone.\n\nÄnderungen an dieser Strecke bitte zuerst mit OK speichern und den Dialog erneut öffnen, damit der Bericht die aktuellen Daten verwendet.')
        report_info.setWordWrap(True)
        report_page.addRow(report_info)
        if record:
            current_profile=self._report_profiles([record]).get(record['id'])
            report_status=QLabel('Geländeprofil für PDF: '+('Vorhanden ('+str(len(current_profile['rows']))+' Profilpunkte)' if current_profile else 'Nicht vorhanden – Geländeprofil erneut berechnen und speichern'))
            report_status.setWordWrap(True)
            report_page.addRow(report_status)
            report_button=QPushButton('PDF-Einzelbericht für diese Richtfunkstrecke erstellen ...')
            report_button.clicked.connect(lambda: self.export_single_link_report(record['id']))
            report_page.addRow(report_button)
            profile_info=QLabel('Für ein neues Geländeprofil: Dialog mit OK schließen, dann links „Verbindung analysieren (Gelände / Fresnel)“ wählen und das Profil berechnen.')
            profile_info.setWordWrap(True)
            report_page.addRow(profile_info)
        else:
            report_info.setText('Bitte die neue Richtfunkstrecke zuerst mit OK speichern. Danach ist der PDF-Einzelbericht hier verfügbar.')
        if dialog.exec()==QDialog.Accepted:
            values=dict(name=name.text(),station_a_id=a.currentData(),station_b_id=b.currentData(),
                        link_type=kind.currentText(),frequency_ghz=freq.value()/1000,
                        bandwidth_mhz=bandwidth.value(),tx_power_dbm=tx.value(),
                        rx_sensitivity_dbm=rx.value(),gain_a_dbi=ga.value(),gain_b_dbi=gb.value(),
                        height_a_m=ha.value(),height_b_m=hb.value(),loss_a_db=la.value(),loss_b_db=lb.value(),reserve_target_db=reserve_target.value(),
                        rain_specific_db_km=rain_specific.value(),gas_specific_db_km=gas_specific.value(),wet_path_fraction=wet_fraction.value(),
                        itu_rain_rate_mm_h=rain_rate.value(),itu_polarization=pol.currentText(),
                        itu_temperature_c=temp_c.value(),itu_pressure_hpa=pressure_hpa.value(),
                        itu_vapor_g_m3=vapor.value(),
                        antenna_a_model_id=antenna_a.currentData(),antenna_b_model_id=antenna_b.currentData(),radio_model_id=radio.currentData())
            try:
                saved_id=self.db.save_radio_link(self.project_id,values,link_id)
                if pending_p530['value'] is not None:
                    actual_id=link_id if link_id is not None else saved_id
                    saved=self.db.radio_link(self.project_id,actual_id) if actual_id is not None else None
                    if saved is not None:
                        existing=self._report_analyses([saved]).get(saved['id']) or {}
                        existing['p530']=pending_p530['value']
                        self._save_report_analysis(saved,existing)
                self.reload_links()
            except (ValueError,Exception) as e:self.alert(e)

    def export_selected_link_report(self):
        item=self.links.currentItem()
        if item is None:return self.alert('Bitte zuerst eine Richtfunkstrecke in der Liste auswählen.')
        self.export_single_link_report(item.data(Qt.UserRole))

    def _report_profiles(self, links):
        from .profile_report_cache import read_profile
        stations={s['id']:s for s in self.db.stations(self.project_id)}
        return {link['id']:profile for link in links if (profile:=read_profile(self.report_profile_folder,self.project_id,link,stations)) is not None}

    def _report_analyses(self, links):
        from .analysis_cache import read_analysis
        stations={s['id']:s for s in self.db.stations(self.project_id)}
        return {link['id']:value for link in links if (value:=read_analysis(self.report_profile_folder,self.project_id,link,stations)) is not None}

    def _save_report_analysis(self, link, value):
        from .analysis_cache import save_analysis
        stations={s['id']:s for s in self.db.stations(self.project_id)}
        save_analysis(self.report_profile_folder,self.project_id,link,stations,value)

    def export_single_link_report(self, link_id):
        if self.project_id is None:return self.alert('Bitte ein Projekt auswählen.')
        link=self.db.radio_link(self.project_id,link_id)
        if link is None:return self.alert('Richtfunkstrecke nicht gefunden.')
        filename,_=QFileDialog.getSaveFileName(self,'Richtfunkbericht speichern','RadioPlanner_Richtfunkstrecke.pdf','PDF-Dateien (*.pdf)')
        if not filename:return
        if not filename.lower().endswith('.pdf'):filename+='.pdf'
        try:
            from .link_report import create_link_report
            create_link_report(filename,self.db.project(self.project_id),[link],self.db.stations(self.project_id),profiles=self._report_profiles([link]),analyses=self._report_analyses([link]),analysis_sink=self._save_report_analysis)
            QMessageBox.information(self,'Richtfunkbericht','PDF-Bericht gespeichert:\n'+filename)
        except Exception as exc:
            QMessageBox.critical(self,'Richtfunkbericht',f'PDF-Erstellung fehlgeschlagen: {exc}')

    def export_all_link_reports(self):
        if self.project_id is None:return self.alert('Bitte ein Projekt auswählen.')
        links=self.db.radio_links(self.project_id)
        if not links:return self.alert('Dieses Projekt enthält keine Richtfunkstrecken.')
        filename,_=QFileDialog.getSaveFileName(self,'Richtfunk-Gesamtbericht speichern','RadioPlanner_Richtfunk_Gesamtbericht.pdf','PDF-Dateien (*.pdf)')
        if not filename:return
        if not filename.lower().endswith('.pdf'):filename+='.pdf'
        try:
            from .link_report import create_link_report
            create_link_report(filename,self.db.project(self.project_id),links,self.db.stations(self.project_id),total=True,profiles=self._report_profiles(links),analyses=self._report_analyses(links),analysis_sink=self._save_report_analysis)
            QMessageBox.information(self,'Richtfunk-Gesamtbericht','PDF-Bericht gespeichert:\n'+filename)
        except Exception as exc:
            QMessageBox.critical(self,'Richtfunk-Gesamtbericht',f'PDF-Erstellung fehlgeschlagen: {exc}')

    def open_profile_dialog(self):
        if self.project_id is None:
            return self.alert('Bitte zuerst ein Projekt auswählen.')
        stations=self.db.stations(self.project_id)
        if len(stations)<2:
            return self.alert('Mindestens zwei gespeicherte Standorte erforderlich.')
        dlg=QDialog(self);dlg.setWindowTitle('Standortverbindung – Geländeprofil und Fresnelzone');dlg.resize(860,570)
        layout=QVBoxLayout(dlg)
        selection=QHBoxLayout();a=QComboBox();b=QComboBox()
        for station in stations:
            a.addItem(station['name'],station['id']);b.addItem(station['name'],station['id'])
        b.setCurrentIndex(1)
        selection.addWidget(QLabel('Standort A'));selection.addWidget(a)
        selection.addWidget(QLabel('Standort B'));selection.addWidget(b)
        layout.addLayout(selection)
        controls=QHBoxLayout()
        freq=self.spin(1,100000,3,5800);ha=self.spin(0,10000,2,25);hb=self.spin(0,10000,2,30)
        k=self.spin(0.1,10,3,4/3)
        fresnel=self.spin(0,100,0,60);fresnel.setSuffix(' %')
        for title,widget in [('MHz',freq),('Höhe A (m AGL)',ha),('Höhe B (m AGL)',hb),('k-Faktor',k),('Freihaltung',fresnel)]:
            controls.addWidget(QLabel(title));controls.addWidget(widget)
        layout.addLayout(controls)
        info=QLabel('Lokal: GeoTIFF (präziser). Online: ca. 90-m-Höhenmodell, nur Vorplanung. Keine Gebäude/Bäume.')
        info.setWordWrap(True);layout.addWidget(info)
        source=QComboBox()
        source.addItem('Lokales GeoTIFF (DGM-Ordner)', 'local')
        source.addItem('Online-Höhenabfrage (ca. 90 m, Vorplanung)', 'online')
        if not self.terrain_folder: source.setCurrentIndex(1)
        layout.addWidget(source)
        map_hint=QLabel('Kartenauswahl: Schaltfläche aktivieren, dann nacheinander zwei Standortmarker anklicken.')
        layout.addWidget(map_hint)
        profile_status=QLabel('PDF-Geländeprofil: Noch nicht für eine gespeicherte Richtfunkstrecke berechnet.')
        profile_status.setWordWrap(True);layout.addWidget(profile_status)
        plot_holder=QVBoxLayout();layout.addLayout(plot_holder,1)
        profile_position=QLabel('Maus über das Geländeprofil bewegen: Kartenposition und Geländewerte erscheinen hier.')
        profile_position.setWordWrap(True);layout.addWidget(profile_position)
        critical_button=QPushButton('Kritischste Stelle anzeigen')
        critical_button.setEnabled(False);layout.addWidget(critical_button)
        profile_state={'result':None,'plot':None,'points':None}
        def show_position(index):
            result=profile_state['result'];points=profile_state['points'];plot=profile_state['plot']
            if not result or not points or plot is None:return
            index=max(0,min(len(points)-1,index));r=result['rows'][index]
            plot.select_index(index)
            lat,lon=points[index]
            reserve=r['clearance_m']-result['fresnel_fraction']*r['fresnel_radius_m']
            profile_position.setText(f"Position {index+1}/{len(points)} | {r['distance_km']:.3f} km ab A | "
                f"{lat:.6f}° N, {lon:.6f}° E\nGelände: {r['terrain_m']:.1f} m | "
                f"Gelände inkl. Erdkrümmung: {r['curved_terrain_m']:.1f} m | "
                f"Sichtlinie: {r['line_m']:.1f} m | Sichtfreiheit: {r['clearance_m']:+.1f} m | "
                f"Fresnel-Freihaltung: {reserve:+.1f} m")
            self.map.show_profile_position(lat,lon)
        def show_critical():
            result=profile_state['result']
            if result:
                index=min(range(len(result['rows'])),key=lambda i:result['rows'][i]['clearance_m']-result['fresnel_fraction']*result['rows'][i]['fresnel_radius_m'])
                show_position(index)
        critical_button.clicked.connect(show_critical)

        selected=self.links.currentItem()
        if selected:
            link=self.db.radio_link(self.project_id,selected.data(Qt.UserRole))
            if link:
                for combo,key in ((a,'station_a_id'),(b,'station_b_id')):
                    for i in range(combo.count()):
                        if combo.itemData(i)==link[key]:combo.setCurrentIndex(i);break
                freq.setValue(link['frequency_ghz']*1000)
                ha.setValue(link['height_a_m']);hb.setValue(link['height_b_m'])
        def calculate():
            if a.currentData()==b.currentData():return self.alert('Bitte zwei unterschiedliche Standorte auswählen.')
            lookup={s['id']:s for s in stations};sa=lookup[a.currentData()];sb=lookup[b.currentData()]
            try:
                result=analyze(self.terrain_folder,(sa['latitude'],sa['longitude']),
                               (sb['latitude'],sb['longitude']),ha.value(),hb.value(),freq.value(),k.value(),
                               online_cache=self.online_cache if source.currentData()=='online' else None,
                               fresnel_fraction=fresnel.value()/100)
            except TerrainError as exc:return self.alert(exc)
            while plot_holder.count():
                item=plot_holder.takeAt(0)
                if item.widget():item.widget().deleteLater()
            plot=ProfilePlot(result);plot_holder.addWidget(plot)
            from .profile import great_circle_points
            points=great_circle_points((sa['latitude'],sa['longitude']),(sb['latitude'],sb['longitude']),len(result['rows']),source.currentData()=='online')
            profile_state.update(result=result,plot=plot,points=points)
            plot.positionChanged.connect(show_position)
            critical_button.setEnabled(True)
            show_position(0)
            from .profile import antenna_alignment
            alignment=antenna_alignment((sa['latitude'],sa['longitude']),(sb['latitude'],sb['longitude']),ha.value(),hb.value(),result['rows'][0]['terrain_m'],result['rows'][-1]['terrain_m'],k.value())
            alignment_note=(f"Azimut A → B: {alignment['azimuth_a_deg']:.2f}° | B → A: {alignment['azimuth_b_deg']:.2f}° | "
                f"Höhenwinkel A: {alignment['elevation_a_deg']:+.3f}° | B: {alignment['elevation_b_deg']:+.3f}°\n"
                f"Effektiver Erdradius k={k.value():.3f} | Erdkrümmungsaufschlag Streckenmitte: {alignment['earth_bulge_mid_m']:.2f} m\n")
            info.setText(alignment_note+f"Entfernung: {result['distance_km']:.3f} km | Minimale Sichtlinienfreiheit: {result['min_clearance_m']:.1f} m | {fresnel.value():g}%-Fresnelreserve: {result['min_fresnel_60_m']:.1f} m\n{result['assessment']}\nQuelle: {result['source']}\nHöhenbezug nicht automatisch geprüft. Keine Vegetations-/Gebäudedaten; keine vollständige Linkverfügbarkeitsbewertung.")
            from .profile import required_antenna_raises
            raises=required_antenna_raises(result)
            height_note=(f"\nErforderliche zusätzliche Antennenhöhe für {fresnel.value():g} % Fresnel-Freihaltung "
                f"(rechnerisch, jeweils alternativ):\n"
                f"Nur Standort A: +{raises['raise_a_only_m']:.2f} m | "
                f"Nur Standort B: +{raises['raise_b_only_m']:.2f} m | "
                f"Beide Standorte: je +{raises['raise_both_each_m']:.2f} m.\n"
                "Nur Geländemodell; keine Bäume, Gebäude, Bau- oder Genehmigungsprüfung.")
            info.setText(info.text()+height_note)
            # Resolve the link by the exact engineering inputs. A list selection may be
            # absent or stale after reload; never assign a profile to a different link.
            chosen=self.links.currentItem()
            selected_id=chosen.data(Qt.UserRole) if chosen else None
            candidates=[]
            for candidate in self.db.radio_links(self.project_id):
                if (candidate['station_a_id']==sa['id'] and candidate['station_b_id']==sb['id']
                    and abs(candidate['frequency_ghz']*1000-freq.value())<0.01
                    and abs(candidate['height_a_m']-ha.value())<0.01
                    and abs(candidate['height_b_m']-hb.value())<0.01):
                    candidates.append(candidate)
            current=next((c for c in candidates if c['id']==selected_id),None)
            if current is None and len(candidates)==1:
                current=candidates[0]
            if current:
                try:
                    from .profile_report_cache import save_profile
                    save_profile(self.report_profile_folder,self.project_id,current,sa,sb,result)
                    terrain_status=('red' if result['min_clearance_m']<0 else 'yellow' if result['min_fresnel_60_m']<0 else 'green')
                    self.db.save_terrain_assessment(self.project_id,current['id'],terrain_status,result)
                    self.reload_links()
                    for index in range(self.links.count()):
                        if self.links.item(index).data(Qt.UserRole)==current['id']:
                            self.links.setCurrentRow(index)
                            break
                    profile_status.setText('PDF-Geländeprofil gespeichert: '+str(len(result['rows']))+' Profilpunkte für '+current['name'])
                except (OSError,ValueError,TypeError) as exc:
                    profile_status.setText('PDF-Geländeprofil konnte nicht gespeichert werden: '+str(exc))
            else:
                profile_status.setText('PDF-Geländeprofil NICHT gespeichert: Keine eindeutig passende gespeicherte Richtfunkstrecke. Bitte Strecke auswählen und Parameter abgleichen.')
            self.map.show_connection(sa,sb)
        click_state={'step':-1}
        def on_map_station(station_id):
            if click_state['step']<0:return
            combo=a if click_state['step']==0 else b
            for i in range(combo.count()):
                if combo.itemData(i)==station_id:
                    combo.setCurrentIndex(i)
                    break
            click_state['step']+=1
            if click_state['step']>=2:
                click_state['step']=-1
                map_hint.setText('Beide Standorte ausgewählt. Jetzt Geländeprofil berechnen.')
            else:
                map_hint.setText('Standort A gewählt. Jetzt Standort B auf der Karte anklicken.')
        def begin_map_selection():
            click_state['step']=0
            map_hint.setText('Jetzt Standort A auf der Karte anklicken (Dialog zur Seite schieben).')
        self.map.bridge.station_selected.connect(on_map_station)
        dlg.finished.connect(lambda _: self.map.bridge.station_selected.disconnect(on_map_station))
        dlg.finished.connect(lambda _: self.map.clear_profile_position())
        buttons=QHBoxLayout();run=QPushButton('Geländeprofil berechnen');run.clicked.connect(calculate);buttons.addWidget(run)
        choose=QPushButton('DGM-Ordner wählen');choose.clicked.connect(self.choose_terrain_folder);buttons.addWidget(choose)
        map_pick=QPushButton('Standorte auf Karte wählen');map_pick.clicked.connect(begin_map_selection);buttons.addWidget(map_pick)
        close=QPushButton('Schließen');close.clicked.connect(dlg.accept);buttons.addWidget(close)
        layout.addLayout(buttons)
        self._profile_dialog = dlg
        dlg.show()

    def delete_link(self):
        item=self.links.currentItem()
        if item is None or self.project_id is None:return
        if QMessageBox.question(self,'Richtfunkverbindung löschen','Verbindung löschen?')==QMessageBox.Yes:
            self.db.delete_radio_link(self.project_id,item.data(Qt.UserRole));self.reload_links()

    def reload_sirens(self, select_id=None):
        self.map_clear_coverage()
        self.sirens.clear()
        records=self.db.sirens(self.project_id) if self.project_id is not None else []
        for siren in records:
            item=QListWidgetItem(siren['name']+' ('+siren['manufacturer']+')')
            item.setData(Qt.UserRole,siren['id']);self.sirens.addItem(item)
            if siren['id']==select_id:self.sirens.setCurrentItem(item)
        self.map.set_sirens([dict(r) for r in records])

    def add_siren(self):
        if self.project_id is None:self.alert('Bitte zuerst ein Projekt wählen.');return
        self.open_siren_dialog()

    def place_siren(self):
        if self.project_id is None:self.alert('Bitte zuerst ein Projekt wählen.');return
        self.map_mode='sirene'
        self.map.set_hint('Sirene setzen: auf die Karte klicken (anderen Modus wählen zum Abbrechen)')
        self.statusBar().showMessage('Sirenenstandort durch Kartenklick auswählen.')

    def move_siren(self):
        item=self.sirens.currentItem()
        if item is None:self.alert('Bitte zuerst eine Sirene auswählen.');return
        self.move_siren_id=item.data(Qt.UserRole)
        self.map_mode='move_siren'
        self.map.set_hint('Neue Sirenenposition anklicken (anderen Modus wählen zum Abbrechen)')

    def relocate_siren(self,siren_id,lat,lon):
        old=self.db.siren(self.project_id,siren_id)
        if old is None:return
        values=dict(old)
        values['latitude']=lat;values['longitude']=lon
        # Previous ground elevation does not apply to the new position.
        for key in ('terrain_height_m','terrain_source','terrain_vertical_datum','terrain_resolution_m'):
            values[key]=None
        try:
            self.db.save_siren(self.project_id,values,siren_id)
            self.reload_sirens(siren_id)
            self.map_clear_coverage()
            self.statusBar().showMessage('Sirene verschoben. Geländehöhe am neuen Ort erneut abfragen; Schallwolke neu berechnen.')
        except Exception as exc:self.alert(exc)

    def edit_siren(self):
        item=self.sirens.currentItem()
        if item is None:self.alert('Bitte eine Sirene auswählen.');return
        self.open_siren_dialog(item.data(Qt.UserRole))

    def delete_siren(self):
        item=self.sirens.currentItem()
        if item is None:return
        if QMessageBox.question(self,'Sirene löschen','Ausgewählte Sirene wirklich löschen?') != QMessageBox.Yes:return
        self.db.delete_siren(self.project_id,item.data(Qt.UserRole));self.reload_sirens();self.map_clear_coverage()

    def manage_siren_models(self):
        dlg=QDialog(self);dlg.setWindowTitle('Eigene Sirenendatenbank');dlg.resize(620,390)
        layout=QVBoxLayout(dlg)
        entries=QListWidget();layout.addWidget(entries)
        def reload():
            entries.clear()
            for r in self.db.siren_models():
                item=QListWidgetItem(f"{r['manufacturer']} – {r['model']} | {r['power_w'] if r['power_w'] is not None else '–'} W | {r['reference_db']} dB bei {r['reference_distance_m']} m")
                item.setData(Qt.UserRole,r['id']);entries.addItem(item)
        def edit(create=False):
            current=entries.currentItem()
            if not create and current is None:return
            old=None if create else self.db.siren_model(current.data(Qt.UserRole))
            editor=QDialog(dlg);editor.setWindowTitle('Sirenenmodell anlegen' if create else 'Sirenenmodell bearbeiten')
            el=QVBoxLayout(editor);form=QFormLayout();el.addLayout(form)
            maker=QLineEdit(old['manufacturer'] if old else '');form.addRow('Hersteller',maker)
            name=QLineEdit(old['model'] if old else '');form.addRow('Modell',name)
            watts=self.spin(0,1000000,1,old['power_w'] if old and old['power_w'] is not None else 1200);form.addRow('Elektrische Nennleistung (W)',watts)
            level=self.spin(0,200,1,old['reference_db'] if old else 120);form.addRow('Gemessener Referenzpegel (dB)',level)
            distance=self.spin(0.1,10000,1,old['reference_distance_m'] if old else 30);form.addRow('Messabstand (m)',distance)
            pattern=QComboBox();pattern.addItems(['Omnidirektional','Unidirektional','Bidirektional','Individuelle Hornanordnung']);form.addRow('Abstrahlung',pattern)
            if old:pattern.setCurrentText(old['pattern'])
            horns=QSpinBox();horns.setRange(1,100);horns.setValue(old['horn_count'] if old else 1);form.addRow('Schalltrichter',horns)
            notes=QPlainTextEdit(old['notes'] if old else '');notes.setMaximumHeight(65);form.addRow('Quelle / Notizen',notes)
            warning=QLabel('Referenzpegel und Messabstand müssen aus einer Messung oder einem Datenblatt stammen.\nWatt allein bestimmt nicht den Schalldruck.');warning.setWordWrap(True);el.addWidget(warning)
            buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Save).setText('Speichern');buttons.button(QDialogButtonBox.Cancel).setText('Abbrechen')
            buttons.accepted.connect(editor.accept);buttons.rejected.connect(editor.reject);el.addWidget(buttons)
            if editor.exec()!=QDialog.Accepted:return
            values=dict(manufacturer=maker.text(),model=name.text(),power_w=watts.value(),reference_db=level.value(),reference_distance_m=distance.value(),pattern=pattern.currentText(),horn_count=horns.value(),notes=notes.toPlainText())
            try:
                self.db.save_siren_model(values,None if create else old['id']);reload();self.map_clear_coverage()
            except Exception as exc:QMessageBox.warning(dlg,'Sirenendatenbank',str(exc))
        def remove():
            current=entries.currentItem()
            if current is None:return
            if QMessageBox.question(dlg,'Modell löschen','Ausgewähltes Modell wirklich löschen?')!=QMessageBox.Yes:return
            try:self.db.delete_siren_model(current.data(Qt.UserRole));reload()
            except Exception as exc:QMessageBox.warning(dlg,'Sirenendatenbank',str(exc))
        bar=QHBoxLayout()
        for title,handler in [('+ Modell',lambda:edit(True)),('Bearbeiten',lambda:edit(False)),('Löschen',remove),('Schließen',dlg.accept)]:
            b=QPushButton(title);b.clicked.connect(handler);bar.addWidget(b)
        layout.addLayout(bar);entries.itemDoubleClicked.connect(lambda _:edit(False));reload();dlg.exec()

    def open_siren_dialog(self,siren_id=None,coords=None):
        old=self.db.siren(self.project_id,siren_id) if siren_id else None
        dialog=QDialog(self);dialog.setWindowTitle('Sirenen-Eigenschaften');layout=QVBoxLayout(dialog);form=QFormLayout();layout.addLayout(form)
        name=QLineEdit(old['name'] if old else '');form.addRow('Name',name)
        lat=self.spin(-90,90,6,old['latitude'] if old else (coords[0] if coords else 49.35));form.addRow('Breite (°)',lat)
        lon=self.spin(-180,180,6,old['longitude'] if old else (coords[1] if coords else 7.1));form.addRow('Länge (°)',lon)
        catalog=QComboBox();catalog.addItem('Manuelle Sireneneigenschaften',None)
        for item in self.db.siren_models():catalog.addItem(f"{item['manufacturer']} – {item['model']}",item['id'])
        form.addRow('Modell aus Sirenendatenbank',catalog)
        manufacturer=QComboBox();manufacturer.addItems(['Telegrafia','HÖRMANN Warnsysteme','Digitex','E57']);form.addRow('Hersteller / Typ',manufacturer)
        model=QLineEdit(old['model'] if old else '');form.addRow('Modell / Konfiguration',model)
        power=QComboBox()
        for v in (300,600,900,1200,1500,1800,2400,3600):power.addItem(f'{v} W',v)
        form.addRow('Elektrische Nennleistung',power)
        power_custom=self.spin(0,1000000,1,1200);form.addRow('Individuelle Leistung (W)',power_custom)
        power.addItem('Eigener Wert',-1)
        power_custom.setVisible(False)
        def toggle_custom():power_custom.setVisible(power.currentData()==-1)
        power.currentIndexChanged.connect(toggle_custom)
        pattern=QComboBox();pattern.addItems(['Omnidirektional','Unidirektional','Bidirektional','Individuelle Hornanordnung']);form.addRow('Abstrahlung',pattern)
        azimuth=self.spin(0,359.99,1,old['azimuth_deg'] if old else 0);form.addRow('Ausrichtung (° von Nord)',azimuth)
        height=self.spin(0,1000,1,old['mounting_height_m'] if old else 12);form.addRow('Montagehöhe (m AGL)',height)
        horns=QSpinBox();horns.setRange(1,100);horns.setValue(old['horn_count'] if old else 1);form.addRow('Anzahl Schalltrichter',horns)
        # Same elevation providers as radio transmitter sites; source metadata is persisted.
        terrain_known=QCheckBox('Geländehöhe bekannt')
        terrain_known.setChecked(bool(old and old['terrain_height_m'] is not None))
        form.addRow('',terrain_known)
        terrain=self.spin(-500,9000,1,old['terrain_height_m'] if old and old['terrain_height_m'] is not None else 0)
        form.addRow('Geländehöhe (m; Höhenbezug siehe Quelle)',terrain)
        source_info={'source':old['terrain_source'] if old else None,
                     'datum':old['terrain_vertical_datum'] if old else None,
                     'resolution':old['terrain_resolution_m'] if old else None}
        source_label=QLabel();source_label.setWordWrap(True)
        form.addRow('Höhendatenquelle',source_label)
        absolute=QLabel();form.addRow('Absolute Sirenenhöhe',absolute)
        def refresh_height():
            terrain.setEnabled(terrain_known.isChecked())
            absolute.setText(f'{terrain.value()+height.value():.1f} m (Höhenbezug der Geländedaten)' if terrain_known.isChecked() else 'Nicht verfügbar')
        def refresh_source():
            source_label.setText(f"{source_info['source'] or 'Manuell / unbekannt'}; Höhenbezug: {source_info['datum'] or 'nicht verifiziert'}" + (f"; Auflösung: {source_info['resolution']} m" if source_info['resolution'] is not None else ''))
        def manual_change(value):
            source_info.update(source='Manuell',datum='Nicht angegeben',resolution=None)
            refresh_source()
        terrain.valueChanged.connect(manual_change)
        terrain.valueChanged.connect(lambda value:refresh_height())
        terrain_known.toggled.connect(lambda checked:refresh_height())
        height.valueChanged.connect(lambda value:refresh_height())
        refresh_source();refresh_height()
        query_buttons=QHBoxLayout()
        local_btn=QPushButton('Lokales GeoTIFF abfragen')
        online_btn=QPushButton('Online-Geländehöhe abfragen')
        query_buttons.addWidget(local_btn);query_buttons.addWidget(online_btn)
        layout.addLayout(query_buttons)
        def query_height(local):
            try:
                if local:
                    if not self.terrain_folder:
                        folder=QFileDialog.getExistingDirectory(dialog,'GeoTIFF-Ordner wählen')
                        if not folder:return
                        self.terrain_folder=folder
                    result=sample_folder(self.terrain_folder,lat.value(),lon.value())
                else:
                    result=sample_online(lat.value(),lon.value(),self.online_cache)
                terrain.blockSignals(True)
                terrain.setValue(result['height'])
                terrain.blockSignals(False)
                terrain_known.setChecked(True)
                source_info.update(source=result['source'],datum=result['vertical_datum'],resolution=result.get('resolution'))
                refresh_source();refresh_height()
            except Exception as exc:
                QMessageBox.warning(dialog,'Höhenabfrage',str(exc))
        local_btn.clicked.connect(lambda:query_height(True))
        online_btn.clicked.connect(lambda:query_height(False))

        import json
        from .acoustic_grid import parse_horn_azimuths
        horn_angles=QLineEdit()
        horn_angles.setPlaceholderText('z. B. 0, 90, 180, 270')
        form.addRow('Trichterrichtungen (°; durch Komma getrennt)',horn_angles)
        horn_help=QLabel('Nur für individuelle Hornanordnung. Ein Winkel pro Trichter, 0° = Nord, 90° = Ost.\nLeeres Feld: Trichter gleichmäßig verteilen (Kompatibilitätsmodus).')
        horn_help.setWordWrap(True);form.addRow('',horn_help)
        if old and old['horn_azimuths']:
            try: horn_angles.setText(', '.join(str(round(float(x),2)) for x in json.loads(old['horn_azimuths'])))
            except (ValueError,TypeError): pass
        def update_horn_input():
            horn_angles.setEnabled('Individ' in pattern.currentText() and manufacturer.currentText()!='E57')
        pattern.currentTextChanged.connect(update_horn_input)
        manufacturer.currentTextChanged.connect(update_horn_input)
        update_horn_input()
        notes=QPlainTextEdit(old['notes'] if old else '');notes.setMaximumHeight(80);form.addRow('Notizen',notes)
        info=QLabel('Hinweis: Leistung in W ist keine akustische Schallleistung.\nSchallausbreitungsberechnung folgt in späterer Version.')
        info.setWordWrap(True);layout.addWidget(info)
        if old:
            manufacturer.setCurrentText(old['manufacturer']);pattern.setCurrentText(old['pattern'])
            if old['power_w'] is not None:
                idx=power.findData(old['power_w']);power.setCurrentIndex(idx if idx>=0 else power.findData(-1));power_custom.setValue(old['power_w'])
        def apply_catalog():
            model_id=catalog.currentData()
            selected=self.db.siren_model(model_id) if model_id is not None else None
            if selected is None:return
            if manufacturer.findText(selected['manufacturer'])<0:manufacturer.addItem(selected['manufacturer'])
            manufacturer.setCurrentText(selected['manufacturer']);model.setText(selected['model'])
            pattern.setCurrentText(selected['pattern']);horns.setValue(selected['horn_count'])
            if selected['power_w'] is not None:
                idx=power.findData(selected['power_w'])
                power.setCurrentIndex(idx if idx>=0 else power.findData(-1))
                power_custom.setValue(selected['power_w'])
        catalog.currentIndexChanged.connect(apply_catalog)
        if old and old['siren_model_id'] is not None:
            idx=catalog.findData(old['siren_model_id'])
            if idx>=0:
                # Loading an existing site's catalog reference must not overwrite
                # its individually saved pattern, horn count, or power.
                catalog.blockSignals(True)
                catalog.setCurrentIndex(idx)
                catalog.blockSignals(False)
        def update_e57():
            mechanical=manufacturer.currentText()=='E57'
            power.setEnabled(not mechanical)
            horns.setEnabled(not mechanical)
        manufacturer.currentTextChanged.connect(update_e57);update_e57()
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Speichern')
        buttons.button(QDialogButtonBox.Cancel).setText('Abbrechen')
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()!=QDialog.Accepted:return
        explicit_angles=[]
        if 'Individ' in pattern.currentText() and manufacturer.currentText()!='E57' and horn_angles.text().strip():
            try:
                explicit_angles=[float(x.strip()) for x in horn_angles.text().replace(';',',').split(',')]
            except ValueError:
                self.alert('Bitte gültige Trichterwinkel eingeben, getrennt durch Kommas.');return
            if len(explicit_angles)!=horns.value() or any(not 0<=x<360 for x in explicit_angles):
                self.alert('Bitte genau einen Winkel von 0 bis unter 360° pro Schalltrichter eingeben.');return
        values=dict(name=name.text(),latitude=lat.value(),longitude=lon.value(),manufacturer=manufacturer.currentText(),
                    model=model.text(),siren_type='Elektromechanisch' if manufacturer.currentText()=='E57' else 'Elektronisch',
                    power_w=None if manufacturer.currentText()=='E57' else (power_custom.value() if power.currentData()==-1 else power.currentData()),pattern=pattern.currentText(),
                    siren_model_id=catalog.currentData(),horn_azimuths=json.dumps(explicit_angles) if explicit_angles else '',
                    azimuth_deg=azimuth.value(),mounting_height_m=height.value(),horn_count=1 if manufacturer.currentText()=='E57' else horns.value(),notes=notes.toPlainText(),
                    terrain_height_m=terrain.value() if terrain_known.isChecked() else None,
                    terrain_source=source_info['source'] if terrain_known.isChecked() else None,
                    terrain_vertical_datum=source_info['datum'] if terrain_known.isChecked() else None,
                    terrain_resolution_m=source_info['resolution'] if terrain_known.isChecked() else None)
        try:
            result=self.db.save_siren(self.project_id,values,siren_id)
            self.reload_sirens(result)
            self.map_clear_coverage()
        except Exception as exc:self.alert(exc)

    def map_clear_coverage(self):
        self.probe_data=None
        self.probe_mode=False
        self.map.set_probe_mode(False)
        self.map.set_coverage({})

    def building_options(self, form):
        enabled=QCheckBox('OSM-Gebäudeabschattung (Näherungsmodell)')
        form.addRow('',enabled)
        height=self.spin(3,80,1,10)
        form.addRow('Ersatz-Gebäudehöhe ohne OSM-Angabe (m)',height)
        loss=self.spin(0,30,1,8)
        form.addRow('Angenommene Abschattung je Sichtlinie (dB)',loss)
        mode=QComboBox()
        mode.addItem('Automatisch (Cache verwenden, fehlende Kacheln laden)', 'auto')
        mode.addItem('Nur offline (keine Internetanfragen)', 'offline')
        mode.addItem('Online aktualisieren (Kacheln neu laden)', 'refresh')
        form.addRow('OSM-Gebäudedaten',mode)
        return enabled,height,loss,mode

    def show_building_cache_manager(self, lat, lon, width_km, height_km):
        from .building_cache import cache_entries, delete_cache_entries, import_legacy_cache
        from .buildings import fetch_buildings, _tile_bounds
        import math
        dlg=QDialog(self)
        dlg.setWindowTitle('OSM-Gebäudedaten verwalten')
        dlg.resize(650,420)
        layout=QVBoxLayout(dlg)
        info=QLabel()
        info.setWordWrap(True)
        layout.addWidget(info)
        listing=QListWidget()
        layout.addWidget(listing)
        def refresh():
            entries=cache_entries(self.building_cache)
            listing.clear()
            for entry in entries:
                listing.addItem(f"{entry['downloaded']}  |  {entry['size_kb']:.1f} KB  |  {entry['name']}")
            info.setText(f"Gemeinsamer Speicherort: {self.building_cache}\n{len(entries)} Kacheln, {sum(e['size_kb'] for e in entries):.1f} KB. Die Datumsangabe ist das Änderungsdatum der Cache-Datei.")
            return entries
        refresh()
        buttons=QHBoxLayout()
        def download():
            # Same latitude/longitude approximation used for analysis bounds.
            dlat=height_km/111.195/2
            dlon=width_km/(111.195*max(.15,math.cos(math.radians(lat))))/2
            bounds=(lat-dlat,lon-dlon,lat+dlat,lon+dlon)
            def task(progress,cancel):
                warnings=[]
                def report(i,n):progress(f'Gebäudedaten laden: Kachel {i}/{n}')
                items=fetch_buildings(*bounds,self.building_cache,progress=report,cancel=cancel,warnings=warnings,mode='auto')
                return len(items),warnings
            def done(result):
                refresh()
                count,warnings=result
                QMessageBox.information(self,'OSM-Gebäudedaten',f'{count} Gebäude verfügbar.' + ('\n'+'\n'.join(warnings) if warnings else ''))
            dlg.accept()
            self.run_calculation('Gebäudedaten herunterladen',task,done)
        btn=QPushButton('Gebiet herunterladen');btn.clicked.connect(download);buttons.addWidget(btn)
        def import_data():
            folder=QFileDialog.getExistingDirectory(dlg,'Ordner einer älteren RadioPlanner-Version wählen')
            if folder:
                count=import_legacy_cache(Path(folder),self.building_cache)
                refresh()
                QMessageBox.information(dlg,'Import',f'{count} Kacheln übernommen. Vorhandene Daten wurden nicht überschrieben.')
        btn=QPushButton('Alten Cache importieren');btn.clicked.connect(import_data);buttons.addWidget(btn)
        def delete_selected():
            selected=listing.currentRow()
            entries=cache_entries(self.building_cache)
            if selected<0 or selected>=len(entries):return
            if QMessageBox.question(dlg,'Kachel löschen','Ausgewählte Kachel wirklich löschen?')==QMessageBox.Yes:
                delete_cache_entries(self.building_cache,[entries[selected]['name']]);refresh()
        btn=QPushButton('Ausgewählte löschen');btn.clicked.connect(delete_selected);buttons.addWidget(btn)
        def delete_all():
            if QMessageBox.question(dlg,'Cache leeren','Alle lokal gespeicherten OSM-Gebäudekacheln löschen?')==QMessageBox.Yes:
                delete_cache_entries(self.building_cache,[e['name'] for e in cache_entries(self.building_cache)]);refresh()
        btn=QPushButton('Alle löschen');btn.clicked.connect(delete_all);buttons.addWidget(btn)
        layout.addLayout(buttons)
        close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(dlg.reject);layout.addWidget(close)
        dlg.exec()

    def load_building_screen(self, enabled, height, loss, records, bounds, progress=None, cancel=None, mode='auto'):
        if not enabled:return None, []
        from .buildings import fetch_buildings, BuildingScreen
        warnings=[]
        def report(current,total):
            if progress:progress(f'OSM-Gebäude laden: Kachel {current}/{total}')
        buildings=fetch_buildings(*bounds,self.building_cache,progress=report,warnings=warnings,cancel=cancel,mode=mode)
        if cancel is not None and cancel.is_set():raise InterruptedError()
        if progress:progress(f'{len(buildings)} Gebäude räumlich indexieren ...')
        return BuildingScreen(buildings,height,loss), warnings

    def run_calculation(self, title, task, on_success):
        if getattr(self,'_calculation_worker',None) is not None:
            self.alert('Es läuft bereits eine Berechnung.');return
        dialog=QProgressDialog('Berechnung wird vorbereitet ...','Abbrechen',0,0,self)
        dialog.setWindowTitle(title)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        worker=CalculationWorker(task,self)
        self._calculation_worker=worker
        self._calculation_dialog=dialog
        dialog.canceled.connect(lambda: worker.cancel_event.set())
        worker.progress.connect(dialog.setLabelText)
        def done(result):
            if not worker.cancel_event.is_set():on_success(result)
        def fail(message):self.alert(title+' fehlgeschlagen: '+message)
        def finish():
            dialog.close()
            self._calculation_worker=None
            self._calculation_dialog=None
            worker.deleteLater()
            dialog.deleteLater()
        worker.completed.connect(done)
        worker.failed.connect(fail)
        worker.finished.connect(finish)
        worker.start()
        dialog.show()

    def show_siren_coverage(self):
        if self.project_id is None:
            self.alert('Bitte zuerst ein Projekt wählen.');return
        records=[dict(x) for x in self.db.sirens(self.project_id)]
        if not records:
            self.alert('Bitte zuerst eine Sirene anlegen.');return
        dlg=QDialog(self);dlg.setWindowTitle('Vereinfachte akustische Schallabdeckung')
        layout=QVBoxLayout(dlg);form=QFormLayout();layout.addLayout(form)
        level=self.spin(60,160,1,120)
        form.addRow('Referenz-Schalldruckpegel in 1 m (dB)',level)
        threshold=self.spin(30,120,1,70)
        form.addRow('Schwellenwert am Empfänger (dB)',threshold)
        attenuation=self.spin(0,10,2,0.005)
        form.addRow('Zusätzliche Dämpfung (dB/m)',attenuation)
        selection=QComboBox()
        selection.addItem('Gesamtes Sirenennetz',None)
        for record in records: selection.addItem(record['name'],record['id'])
        current=self.sirens.currentItem()
        if current is not None:
            idx=selection.findData(current.data(Qt.UserRole))
            if idx>=0: selection.setCurrentIndex(idx)
        form.addRow('Berechnungsumfang',selection)
        terrain_enabled=QCheckBox('Geländeabschattung näherungsweise berücksichtigen (Online-Höhendaten)')
        form.addRow('',terrain_enabled)
        building_enabled,building_height,building_loss,building_mode=self.building_options(form)
        area_mode=QComboBox()
        area_mode.addItem('Automatisch (bisheriges Verhalten)', 'auto')
        area_mode.addItem('Manuell: Rechteck um Mittelpunkt', 'manual')
        form.addRow('Berechnungsgebiet',area_mode)
        area_width=self.spin(0.2,50,1,3.0)
        area_height=self.spin(0.2,50,1,3.0)
        form.addRow('Breite (km)',area_width)
        form.addRow('Höhe (km)',area_height)
        center_choice=QComboBox()
        center_choice.addItem('Mittelpunkt des ausgewählten Sirenennetzes', 'network')
        for r in records:center_choice.addItem('Sirene: '+r['name'],r['id'])
        center_choice.addItem('Frei eingegebene Koordinaten', 'custom')
        form.addRow('Mittelpunkt',center_choice)
        center_lat_input=self.spin(-85,85,6,sum(float(r['latitude']) for r in records)/len(records))
        center_lon_input=self.spin(-180,180,6,sum(float(r['longitude']) for r in records)/len(records))
        form.addRow('Mittelpunkt Breitengrad',center_lat_input)
        form.addRow('Mittelpunkt Längengrad',center_lon_input)
        tile_preview=QLabel('')
        form.addRow('OSM-Kacheln (Schätzung)',tile_preview)
        def selected_records():
            chosen=selection.currentData()
            return [r for r in records if chosen is None or r['id']==chosen]
        def current_center():
            choice=center_choice.currentData()
            if choice=='custom':return center_lat_input.value(),center_lon_input.value()
            if choice=='network':
                rs=selected_records()
                return (sum(float(r['latitude']) for r in rs)/len(rs),sum(float(r['longitude']) for r in rs)/len(rs))
            r=next((r for r in records if r['id']==choice),selected_records()[0])
            return float(r['latitude']),float(r['longitude'])
        def manual_bounds():
            import math as _math
            from .acoustic_grid import R
            lat,lon=current_center()
            dlat=area_height.value()*500/R*180/_math.pi
            dlon=area_width.value()*500/R*180/_math.pi/max(.15,_math.cos(_math.radians(lat)))
            return lat-dlat,lon-dlon,lat+dlat,lon+dlon
        def update_area_ui(*_):
            manual=area_mode.currentData()=='manual'
            for widget in (area_width,area_height,center_choice):widget.setEnabled(manual)
            custom=manual and center_choice.currentData()=='custom'
            center_lat_input.setEnabled(custom);center_lon_input.setEnabled(custom)
            if manual:
                from .buildings import _tile_bounds
                try:
                    count=len(list(_tile_bounds(*manual_bounds())))
                    tile_preview.setText(f'{count} Kachel(n) bei gewähltem Gebiet')
                except ValueError as exc:tile_preview.setText(str(exc))
            else:tile_preview.setText('Automatisch; abhängig von Sirenen und Reichweite')
        for widget in (area_mode,center_choice,selection):widget.currentIndexChanged.connect(update_area_ui)
        for widget in (area_width,area_height,center_lat_input,center_lon_input):widget.valueChanged.connect(update_area_ui)
        update_area_ui()
        info=QLabel('Nur ein schematisches Freifeldmodell: Lp(r) = Lp(1 m) − 20 log10(r) − Dämpfung × (r−1).\n'
                    'Referenzpegel muss aus Messung oder Datenblatt stammen. Elektrische Wattzahl wird NICHT in dB umgerechnet.\n'
                    'Optional: vereinfachte Geländesichtlinien-Abschattung, KEINE validierte Beugungsrechnung.\n'
                    'Gebäude optional als vereinfachte Sichtlinien-Abschattung; keine Beugung oder Reflexion.\n'
                    'Glatte Richtungsdämpfung ist eine Modellannahme, keine gemessene Richtcharakteristik.\n'
                    'Eigene Sirenenmodelle verwenden stattdessen ihren gespeicherten Referenzpegel und Messabstand.')
        info.setWordWrap(True);layout.addWidget(info)
        values_info=QLabel('Verwendete Sirenenparameter: Richtcharakteristik, Azimut, Anzahl Trichter; Montagehöhe nur bei aktivierter Geländeabschattung.\nElektrische Leistung wird mangels Hersteller-Schalldaten nicht berücksichtigt.')
        values_info.setWordWrap(True);layout.addWidget(values_info)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Anzeigen')
        buttons.button(QDialogButtonBox.Cancel).setText('Abbrechen')
        buttons.accepted.connect(dlg.accept);buttons.rejected.connect(dlg.reject);layout.addWidget(buttons)
        if dlg.exec()!=QDialog.Accepted:return
        chosen=selection.currentData()
        if chosen is not None: records=[r for r in records if r['id']==chosen]
        from .acoustic_grid import field, R
        from .acoustics import range_for_level
        import math as _math
        for r in records:
            selected=self.db.siren_model(r['siren_model_id']) if r.get('siren_model_id') else None
            if selected:
                r['acoustic_reference_db']=selected['reference_db']
                r['acoustic_reference_distance_m']=selected['reference_distance_m']
        reference_db=level.value(); damping=attenuation.value(); min_db=threshold.value()
        use_terrain=terrain_enabled.isChecked();use_buildings=building_enabled.isChecked()
        default_height=building_height.value();building_db=building_loss.value();osm_mode=building_mode.currentData()
        radius=min(12000.,max(250.,max(range_for_level(float(r.get('acoustic_reference_db',reference_db))+20*_math.log10(float(r.get('acoustic_reference_distance_m',1))),min_db-18,damping) for r in records)))
        latitudes=[float(r['latitude']) for r in records];longitudes=[float(r['longitude']) for r in records]
        dlat=radius/R*180/_math.pi;dlon=dlat/max(.15,_math.cos(_math.radians(sum(latitudes)/len(latitudes))))
        bounds=(min(latitudes)-dlat,min(longitudes)-dlon,max(latitudes)+dlat,max(longitudes)+dlon)
        manual_area=area_mode.currentData()=='manual'
        if manual_area:bounds=manual_bounds()
        def task(progress,cancel):
            screen=None
            if use_terrain:
                progress('Geländedaten laden ...')
                from .acoustic_terrain import terrain_screen
                screen=terrain_screen(records,cache_folder=self.online_cache)
            buildings,warnings=self.load_building_screen(use_buildings,default_height,building_db,records,bounds,progress,cancel,osm_mode)
            if cancel.is_set():raise InterruptedError()
            progress('Schallpegelwolke berechnen ...')
            area_bounds=bounds if manual_area else None
            raster=field(records,reference_db,damping,min_db,terrain_screen=screen,building_screen=buildings,cancel=cancel,bounds=area_bounds)
            variants=None
            if buildings is not None:
                progress('Vergleich ohne Gebäude berechnen ...')
                without=field(records,reference_db,damping,min_db,terrain_screen=screen,cancel=cancel,bounds=area_bounds)
                progress('Differenzkarte berechnen ...')
                difference=field(records,reference_db,damping,min_db,terrain_screen=screen,building_screen=buildings,cancel=cancel,bounds=area_bounds,render_mode='difference')
                variants={'with':raster,'without':without,'difference':difference}
            return raster,screen,buildings,warnings,variants
        def success(result):
            raster,screen,buildings,warnings,variants=result
            if buildings is not None:
                # Limit payload size to keep Qt WebEngine responsive on large datasets.
                display_buildings=[{'polygon':poly} for poly,height,box in buildings.buildings[:12000]]
                self.map.set_buildings(display_buildings)
            else:
                self.map.set_buildings([])
            if variants:self.map.set_coverage_comparison(variants)
            else:self.map.set_coverage(raster)
            self._coverage_comparison_ready=bool(variants)
            self.probe_data={'sirens':records,'reference_db':reference_db,'attenuation':damping,'threshold':min_db,'terrain_screen':screen,'building_screen':buildings}
            self.probe_mode=False
            self.map.set_probe_mode(False)
            if warnings:self.alert('Achtung: '+' '.join(warnings))
            self.statusBar().showMessage('Schallpegelwolke berechnet.',15000)
        self.run_calculation('Schallberechnung',task,success)

    def show_coverage_gaps(self, compare=False):
        if self.project_id is None:
            self.alert('Bitte zuerst ein Projekt wählen.');return
        records=[dict(x) for x in self.db.sirens(self.project_id)]
        if not records:
            self.alert('Bitte zuerst eine Sirene anlegen.');return
        polygon=self.analysis_polygon
        if polygon:
            minlat=min(pt[0] for pt in polygon);maxlat=max(pt[0] for pt in polygon)
            minlon=min(pt[1] for pt in polygon);maxlon=max(pt[1] for pt in polygon)
            center_lat=(minlat+maxlat)/2;center_lon=(minlon+maxlon)/2
        dlg=QDialog(self);dlg.setWindowTitle('Versorgungslücken und Flächenanalyse')
        layout=QVBoxLayout(dlg);form=QFormLayout();layout.addLayout(form)
        center_lat=sum(float(r['latitude']) for r in records)/len(records)
        center_lon=sum(float(r['longitude']) for r in records)/len(records)
        lat=self.spin(-85,85,6,center_lat);lon=self.spin(-180,180,6,center_lon)
        width=self.spin(0.1,50,1,6);height=self.spin(0.1,50,1,6)
        if polygon:
            width.setValue(max(0.1,min(50, (maxlon-minlon)*111.195*math.cos(math.radians(center_lat))*1.002)))
            height.setValue(max(0.1,min(50, (maxlat-minlat)*111.195*1.002)))
        minimum=self.spin(30,120,1,70)
        reference=self.spin(60,160,1,120)
        attenuation=self.spin(0,10,3,0.005)
        for name,widget in [('Mittelpunkt Breitengrad',lat),('Mittelpunkt Längengrad',lon),
                            ('Breite des Untersuchungsgebiets (km)',width),
                            ('Höhe des Untersuchungsgebiets (km)',height),
                            ('Mindestpegel (dB)',minimum),('Ersatz-Referenzpegel bei 1 m (dB)',reference),
                            ('Zusätzliche Dämpfung (dB/m)',attenuation)]:form.addRow(name,widget)
        selection=QComboBox();selection.addItem('Gesamtes Sirenennetz',None)
        for r in records:selection.addItem(r['name'],r['id'])
        form.addRow('Auswertung',selection)
        terrain=QCheckBox('Geländeabschattung näherungsweise berücksichtigen (Online-Höhendaten)')
        form.addRow('',terrain)
        building_enabled,building_height,building_loss,building_mode=self.building_options(form)
        # Shared acoustic controls are inside this popup, NOT the sidebar.
        tools=QGroupBox('Schallpegelwolke und Kartendarstellung')
        tools_layout=QVBoxLayout(tools)
        def popup_action(label, callback):
            button=QPushButton(label)
            def run():
                dlg.reject()
                callback()
            button.clicked.connect(run)
            tools_layout.addWidget(button)
            return button
        popup_action('Schallpegelwolke berechnen ...', self.show_siren_coverage)
        manage_cache=QPushButton('OSM-Gebäudecache verwalten ...')
        manage_cache.clicked.connect(lambda: self.show_building_cache_manager(lat.value(),lon.value(),width.value(),height.value()))
        tools_layout.addWidget(manage_cache)
        self.show_buildings_checkbox=QCheckBox('OSM-Gebäude anzeigen')
        self.show_buildings_checkbox.setChecked(True)
        self.show_buildings_checkbox.toggled.connect(self.map.toggle_buildings)
        tools_layout.addWidget(self.show_buildings_checkbox)
        tools_layout.addWidget(QLabel('Schallpegelvergleich'))
        self.comparison_mode=QComboBox()
        self.comparison_mode.addItem('Mit Gebäudeabschattung','with')
        self.comparison_mode.addItem('Ohne Gebäudeabschattung','without')
        self.comparison_mode.addItem('Differenzkarte (Dämpfung)','difference')
        self.comparison_mode.setEnabled(bool(getattr(self,'_coverage_comparison_ready',False)))
        self.comparison_mode.currentIndexChanged.connect(lambda _:self.map.select_coverage_variant(self.comparison_mode.currentData()))
        tools_layout.addWidget(self.comparison_mode)
        popup_action('Schallpegel auf Karte abfragen', self.toggle_probe_mode)
        popup_action('Schallabdeckung ausblenden', self.map_clear_coverage)
        layout.addWidget(tools)
        results=QGroupBox('Ergebnisse und Planungsbericht')
        results_layout=QVBoxLayout(results)
        if self.last_area_report:
            r=self.last_area_report['result']
            summary=(f"Untersuchungsgebiet: {r['area_km2']:.2f} km² | "
                     f"Versorgt: {r['served_km2']:.2f} km² ({r['served_percent']:.1f} %) | "
                     f"Unversorgt: {r['gap_km2']:.2f} km² ({r['gap_percent']:.1f} %)")
        else:
            summary='Noch keine Flächenanalyse durchgeführt.'
        summary_label=QLabel(summary);summary_label.setWordWrap(True)
        results_layout.addWidget(summary_label)
        report_button=QPushButton('PDF-Planungsbericht erstellen ...')
        report_button.setEnabled(self.last_area_report is not None)
        report_button.clicked.connect(self.export_area_report)
        results_layout.addWidget(report_button)
        results_layout.addWidget(QLabel('Die Karte zeigt die zuletzt berechnete Schallpegel- oder Flächenansicht.'))
        layout.addWidget(results)
        simulation=QGroupBox('Untersuchungsgebiet und Simulation')
        simulation_layout=QVBoxLayout(simulation)
        for title,handler in [
            ('Untersuchungsgebiet zeichnen',self.start_area_polygon),
            ('Polygon übernehmen',self.map_finish_polygon),
            ('Sirenenstandort simulieren',self.start_siren_simulation),
            ('Simulation vergleichen',self.compare_simulation),
            ('Simulation verwerfen',self.discard_simulation),
        ]:
            button=QPushButton(title)
            button.clicked.connect(lambda _checked=False, fn=handler: (dlg.reject(),fn()))
            simulation_layout.addWidget(button)
        layout.addWidget(simulation)
        note=QLabel(('Gezeichnetes Polygon' if polygon else 'Rechteckiges Untersuchungsgebiet')+' – Grün: Mindestpegel erreicht; Rot: darunter.\n'
                    'Flächenanteile basieren auf einem gleichmäßig verteilten Berechnungsraster.\n'
                    'Keine Hintergrundgeräusche, keine validierte Warnabdeckungs- oder Beugungsprognose.')
        note.setWordWrap(True);layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Berechnen')
        buttons.button(QDialogButtonBox.Cancel).setText('Abbrechen')
        buttons.accepted.connect(dlg.accept);buttons.rejected.connect(dlg.reject);layout.addWidget(buttons)
        if dlg.exec()!=QDialog.Accepted:return
        chosen=selection.currentData()
        if chosen is not None:records=[r for r in records if r['id']==chosen]
        from .coverage_analysis import analyze
        from .acoustic_grid import R
        center=(lat.value(),lon.value());area=(width.value(),height.value())
        min_db=minimum.value();ref_db=reference.value();damping=attenuation.value()
        use_terrain=terrain.isChecked();use_buildings=building_enabled.isChecked()
        default_height=building_height.value();building_db=building_loss.value();osm_mode=building_mode.currentData()
        for r in records:
            model=self.db.siren_model(r['siren_model_id']) if r.get('siren_model_id') else None
            if model:
                r['acoustic_reference_db']=model['reference_db']
                r['acoustic_reference_distance_m']=model['reference_distance_m']
        extra=None
        if compare and self.simulated_siren:
            extra=dict(self.simulated_siren)
            model=self.db.siren_model(extra['siren_model_id']) if extra.get('siren_model_id') else None
            if model:
                extra['acoustic_reference_db']=model['reference_db']
                extra['acoustic_reference_distance_m']=model['reference_distance_m']
        dlat=area[1]*500/R*180/math.pi
        dlon=area[0]*500/(R*math.cos(math.radians(center[0])))*180/math.pi
        bounds=(center[0]-dlat,center[1]-dlon,center[0]+dlat,center[1]+dlon)
        def task(progress,cancel):
            screen=None
            if use_terrain:
                progress('Geländedaten laden ...')
                from .acoustic_terrain import terrain_screen
                screen=terrain_screen(records,cache_folder=self.online_cache)
            buildings,warnings=self.load_building_screen(use_buildings,default_height,building_db,records,bounds,progress,cancel,osm_mode)
            if cancel.is_set():raise InterruptedError()
            progress('Versorgungsfläche berechnen ...')
            before=analyze(records,*center,*area,min_db,ref_db,damping,screen,polygon=polygon,building_screen=buildings,cancel=cancel)
            result=before
            if extra is not None:
                progress('Vergleichsfläche berechnen ...')
                result=analyze(records+[extra],*center,*area,min_db,ref_db,damping,screen,polygon=polygon,building_screen=buildings,cancel=cancel)
            return result,before,screen,buildings,warnings
        def success(payload):
            result,before,screen,buildings,warnings=payload
            if warnings:self.alert('Achtung: '+' '.join(warnings))
            if extra is not None:
                QMessageBox.information(self,'Vorher-Nachher-Vergleich (Näherung)',
                    f'Vorher: {before["served_km2"]:.2f} km² ({before["served_percent"]:.1f} %)\n'
                    f'Mit simulierter Sirene: {result["served_km2"]:.2f} km² ({result["served_percent"]:.1f} %)\n'
                    f'Gewinn: {result["served_km2"]-before["served_km2"]:+.2f} km² / '
                    f'{result["served_percent"]-before["served_percent"]:+.1f} Prozentpunkte\n\n'
                    'Simulation nicht gespeichert. Näherungsmodell, keine validierte Warnabdeckung.')
            self.map.set_coverage(result)
            self.last_area_report={
                'result':result,
                'center':center,'area':area,'minimum_db':min_db,
                'reference_db':ref_db,'attenuation':damping,
                'terrain':use_terrain,'buildings':use_buildings,
                'osm_mode':osm_mode,
                'sirens':[dict(r) for r in records],
                'polygon':bool(polygon),'warnings':list(warnings),
                'comparison': ({'before':before,'after':result} if extra is not None else None)
            }
            self.probe_data={'sirens':records,'reference_db':ref_db,
                             'attenuation':damping,'threshold':min_db,'terrain_screen':screen,'building_screen':buildings}
            self.probe_mode=False;self.map.set_probe_mode(False)
            QMessageBox.information(self,'Flächenanalyse (Näherung)',
                f"Untersuchungsgebiet: {result['area_km2']:.2f} km²\n"
                f"Mindestpegel erreicht: {result['served_km2']:.2f} km² ({result['served_percent']:.1f} %)\n"
                f"Unter Mindestpegel: {result['gap_km2']:.2f} km² ({result['gap_percent']:.1f} %)\n\n"
                'Die Prozentwerte beziehen sich ausschließlich auf das Untersuchungsgebiet.\n'
                'Vereinfachtes Modell, keine nachgewiesene Warnabdeckung.')

        self.run_calculation('Flächenanalyse',task,success)

    def export_area_report(self):
        """Export the most recent completed area analysis as a PDF."""
        if not self.last_area_report:
            self.alert('Bitte zuerst eine Flächenanalyse berechnen.')
            return
        dlg=QDialog(self)
        dlg.setWindowTitle('PDF-Planungsbericht – Vorschau und Inhalt')
        layout=QVBoxLayout(dlg)
        report=self.last_area_report
        result=report['result']
        overview=QLabel(
            f"Versorgt: {result['served_km2']:.2f} km² ({result['served_percent']:.1f} %)\n"
            f"Unversorgt: {result['gap_km2']:.2f} km² ({result['gap_percent']:.1f} %)\n"
            'Vereinfachtes Modell – keine validierte Schallprognose.')
        layout.addWidget(overview)
        include_map=QCheckBox('Aktuelle Kartenansicht aufnehmen')
        include_map.setChecked(True);layout.addWidget(include_map)
        include_sirens=QCheckBox('Sirenenstandorte auflisten')
        include_sirens.setChecked(True);layout.addWidget(include_sirens)
        include_settings=QCheckBox('Berechnungseinstellungen aufführen')
        include_settings.setChecked(True);layout.addWidget(include_settings)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('PDF speichern ...')
        buttons.accepted.connect(dlg.accept);buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec()!=QDialog.Accepted:return
        filename,_=QFileDialog.getSaveFileName(self,'Planungsbericht speichern','RadioPlanner_Planungsbericht.pdf','PDF-Dateien (*.pdf)')
        if not filename:return
        if not filename.lower().endswith('.pdf'):filename+='.pdf'
        try:
            from .report_export import create_report
            from datetime import datetime
            screenshot=None
            if include_map.isChecked():
                image=self.map.grab()
                if not image.isNull():
                    from PySide6.QtCore import QBuffer,QIODevice
                    buffer=QBuffer();buffer.open(QIODevice.WriteOnly)
                    if image.save(buffer,'PNG'):
                        screenshot=bytes(buffer.data())
                    buffer.close()
            create_report(filename,report,
                          map_png=screenshot,
                          show_sirens=include_sirens.isChecked(),
                          show_settings=include_settings.isChecked(),
                          generated_at=datetime.now())
            QMessageBox.information(self,'PDF-Planungsbericht','Bericht gespeichert:\n'+filename)
        except Exception as exc:
            QMessageBox.warning(self,'PDF-Export fehlgeschlagen',str(exc))

    def closeEvent(self,event):
        worker=getattr(self,'_calculation_worker',None)
        if worker is not None:
            worker.cancel_event.set()
            self.statusBar().showMessage('Bitte warten: Berechnung wird abgebrochen ...')
            event.ignore()
            return
        self.db.close()
        super().closeEvent(event)

def main():
    app=QApplication(sys.argv)
    app.setApplicationName('RadioPlanner')
    app.setOrganizationName('RadioPlanner')
    app.setStyle('Fusion')
    window=MainWindow();window.show()
    sys.exit(app.exec())

if __name__=='__main__':main()

"""PDF reporting from persisted project/link data; never invent missing analyses."""
from datetime import datetime
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.graphics.shapes import Drawing, PolyLine, Polygon, String, Line
from .profile import distance_m, antenna_alignment, required_antenna_raises
from .link_budget import calculate, status_for_margin


def create_link_report(filename, project, links, stations, total=False, profiles=None, analyses=None, analysis_sink=None):
    profiles=profiles or {}
    analyses=analyses or {}
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='RPHeading',parent=styles['Heading1'],fontSize=17,leading=21,textColor=colors.HexColor('#174A58'),spaceAfter=13))
    styles.add(ParagraphStyle(name='RPSub',parent=styles['Heading2'],fontSize=12,textColor=colors.HexColor('#174A58'),spaceBefore=13,spaceAfter=7))
    styles.add(ParagraphStyle(name='RPBody',parent=styles['Normal'],fontSize=9,leading=13,spaceAfter=5))
    styles.add(ParagraphStyle(name='RPCell',parent=styles['Normal'],fontSize=8,leading=11))
    station_map={s['id']:s for s in stations}
    def para(t,style='RPBody'):return Paragraph(escape(str(t)),styles[style])
    def section(story,title,rows):
        story.append(para(title,'RPSub'))
        cells=[[para(a,'RPCell'),para(b,'RPCell')] for a,b in rows]
        t=Table(cells,colWidths=[6.0*cm,10.3*cm],hAlign='LEFT',repeatRows=0)
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,0),(-1,-1),[colors.white,colors.HexColor('#F2F6F7')]),('LINEBELOW',(0,0),(-1,-1),.25,colors.HexColor('#D6E0E2')),('LEFTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story.append(t)
    project_name=project['name'] if project else 'Unbenanntes Projekt'
    story=[para('RadioPlanner | Richtfunk-Gesamtbericht' if total else 'RadioPlanner | Richtfunk-Streckenbericht','RPHeading'),para('Projekt: '+project_name),para('Erstellt: '+datetime.now().strftime('%d.%m.%Y %H:%M')),Spacer(1,10)]
    if total and links:
        section(story,'Streckenübersicht',[(str(i)+'. '+link['name'],f"{link['frequency_ghz']:.3f} GHz | {link['link_type']}") for i,link in enumerate(links,1)])
        story.append(PageBreak())
    for index,link in enumerate(links):
        if index:story.append(PageBreak())
        story.append(para(f"{index+1}. {link['name']}",'RPHeading'))
        a=station_map.get(link['station_a_id']);b=station_map.get(link['station_b_id'])
        if not a or not b:
            story.append(para('Standortdaten fehlen; keine Berechnung möglich.'))
            continue
        coords_a=(a['latitude'],a['longitude']);coords_b=(b['latitude'],b['longitude'])
        d=distance_m(coords_a,coords_b)/1000
        section(story,'Standorte und Ausrichtung',[
            ('Standort A',f"{a['name']} | {a['latitude']:.6f}°, {a['longitude']:.6f}°"),
            ('Standort B',f"{b['name']} | {b['latitude']:.6f}°, {b['longitude']:.6f}°"),
            ('Streckenlänge',f'{d:.3f} km'),
            ('Antennenhöhe über Grund',f"A: {link['height_a_m']:.2f} m | B: {link['height_b_m']:.2f} m"),
        ])
        alignment=antenna_alignment(coords_a,coords_b)
        section(story,'Antennenausrichtung',[
            ('Azimut A nach B',f"{alignment['azimuth_a_deg']:.2f}°"),
            ('Azimut B nach A',f"{alignment['azimuth_b_deg']:.2f}°"),
            ('Höhenwinkel','Nicht berechnet: verifizierte Geländehöhen erforderlich'),
        ])
        section(story,'Funktechnik',[
            ('Frequenz / Zuteilung',f"{link['frequency_ghz']:.3f} GHz | {link['link_type']}"),
            ('Bandbreite',f"{link['bandwidth_mhz']:.2f} MHz"),
            ('Sendeleistung',f"{link['tx_power_dbm']:.2f} dBm"),
            ('Antennengewinn A / B',f"{link['gain_a_dbi']:.2f} / {link['gain_b_dbi']:.2f} dBi"),
            ('Kabelverluste A / B',f"{link['loss_a_db']:.2f} / {link['loss_b_db']:.2f} dB"),
            ('Empfängerempfindlichkeit',f"{link['rx_sensitivity_dbm']:.2f} dBm"),
            ('Gerätemodell-ID',str(link['radio_model_id'] or 'Nicht hinterlegt')),
            ('Antennenmodell-IDs',f"A: {link['antenna_a_model_id'] or '—'} | B: {link['antenna_b_model_id'] or '—'}"),
        ])
        budget=None
        try:
            budget=calculate(d,link['frequency_ghz']*1000,link['tx_power_dbm'],link['gain_a_dbi'],link['gain_b_dbi'],link['loss_a_db'],link['loss_b_db'],link['rx_sensitivity_dbm'],link['reserve_target_db'])
            section(story,'Link-Budget',[
                ('Freiraumdämpfung',f"{budget['fspl_db']:.2f} dB"),
                ('EIRP',f"{budget['eirp_dbm']:.2f} dBm"),
                ('Empfangspegel',f"{budget['received_dbm']:.2f} dBm"),
                ('Fadingreserve',f"{budget['fade_margin_db']:.2f} dB"),
                ('Zielreserve',f"{link['reserve_target_db']:.2f} dB"),
                ('Bewertung',budget['assessment']),
            ])
        except ValueError as exc:story.append(para('Link-Budget nicht berechenbar: '+str(exc)))
        terrain_status={'green':'Freihaltung erfüllt','yellow':'Fresnel-Freihaltung eingeschränkt','red':'Sichtlinie behindert','unknown':'Nicht geprüft'}
        profile=profiles.get(link['id'])
        # The persisted profile is the authoritative analysis when legacy summary
        # columns are empty (e.g. after importing an .rplan project).
        status_value=link['terrain_status']
        source_value=link['terrain_source']
        clearance_value=link['terrain_clearance_m']
        fresnel_value=link['terrain_fresnel_clearance_m']
        if profile:
            clearance_value=profile.get('min_clearance_m') if clearance_value is None else clearance_value
            fresnel_value=profile.get('min_fresnel_60_m') if fresnel_value is None else fresnel_value
            source_value=source_value or profile.get('source')
            if status_value in (None,'unknown') and clearance_value is not None and fresnel_value is not None:
                status_value='red' if clearance_value < 0 else 'yellow' if fresnel_value < 0 else 'green'
        cached=analyses.get(link['id']) or {}
        mast_text='Nicht verfügbar: Geländeprofil für diese Strecke berechnen'
        if profile:
            try:
                raises=required_antenna_raises(profile)
                cached['mast_heights']=raises
                mast_text=(f"Alternativen: nur A +{raises['raise_a_only_m']:.2f} m; "
                           f"nur B +{raises['raise_b_only_m']:.2f} m; "
                           f"beide je +{raises['raise_both_each_m']:.2f} m")
            except (KeyError, ValueError, TypeError) as exc:
                mast_text='Nicht berechenbar: '+str(exc)
        section(story,'Gelände und Fresnel',[
            ('Geländeprüfung',terrain_status.get(status_value,'Nicht geprüft')),
            ('Datenquelle',source_value or 'Nicht dokumentiert'),
            ('Sichtlinienfreiheit',f"{clearance_value:.2f} m" if clearance_value is not None else 'Nicht berechnet'),
            ('Fresnel-Freihaltung',f"{fresnel_value:.2f} m" if fresnel_value is not None else 'Nicht berechnet'),
            ('Geländeprofilgrafik','Aus gespeicherter Profilberechnung' if profile else 'Nicht verfügbar: bitte Geländeprofil für diese Strecke neu berechnen'),
            ('Zusätzliche Masthöhen (Geländemodell)',mast_text),
        ])
        if profile:
            story.append(para('Geländeprofil mit Sichtlinie und Fresnelzone','RPSub'))
            story.append(terrain_drawing(profile))
            story.append(para('Quelle: '+str(profile.get('source','Unbekannt'))+' | k='+str(profile.get('k','?'))+' | Freihaltung: '+str(round(profile.get('fresnel_fraction',0.6)*100))+' %. Höhenbezug prüfen; keine Gebäude oder Vegetation.'))
        availability_text='Nicht berechnet'
        availability_data=None
        try:
            from .propagation import itu_specific_losses, rain_availability_estimate
            if not isinstance(budget,dict):
                raise ValueError('Link-Budget fehlt')
            losses=itu_specific_losses(link['frequency_ghz']*1000,link['itu_rain_rate_mm_h'],
                link['itu_polarization'],link['itu_temperature_c'],
                link['itu_pressure_hpa'],link['itu_vapor_g_m3'])
            gas=losses['gas_specific_db_km']*d
            est=rain_availability_estimate((a['latitude']+b['latitude'])/2,
                (a['longitude']+b['longitude'])/2,d,link['frequency_ghz']*1000,
                link['itu_polarization'],budget['fade_margin_db'],gas)
            availability_data={'estimate':est,'fade_margin_db':budget['fade_margin_db'],'gas_loss_db':gas}
            if est['status']=='insufficient':
                availability_text='Nicht belastbar: '+est['note']
            else:
                comparator={'estimate':'ca.','upper_bound':'<','lower_bound':'>'}.get(est['status'],'ca.')
                availability_text=(f"Regenbedingter Ausfallanteil {comparator} {est['p_percent']:.4f} %/Jahr; "
                    f"Ausfallzeit {comparator} {est['outage_hours_year']:.2f} h/Jahr; "
                    f"Verfügbarkeit {('ca.' if est['status']=='estimate' else 'Grenzwert')} "
                    f"{est['availability_percent']:.4f} %; R0,01 {est['r001_mm_h']:.2f} mm/h. "
                    f"{est['note']}")
        except (RuntimeError, ValueError, TypeError, KeyError, ImportError) as exc:
            availability_text='Nicht berechenbar: '+str(exc)
        if availability_data is not None:
            cached['p530']=availability_data
        elif cached.get('p530'):
            availability_text='Gespeichertes P.530-Ergebnis (Berechnung aktuell nicht verfügbar): '+str(cached['p530'].get('estimate',{}))
        if analysis_sink is not None and cached:
            analysis_sink(link,cached)
        section(story,'ITU-R-Eingaben und Dämpfungsszenario',[
            ('Regenrate (gespeicherte Eingabe)',f"{link['itu_rain_rate_mm_h']:.2f} mm/h"),
            ('Polarisation',link['itu_polarization']),
            ('Temperatur / Luftdruck',f"{link['itu_temperature_c']:.1f} °C / {link['itu_pressure_hpa']:.1f} hPa"),
            ('Wasserdampfdichte',f"{link['itu_vapor_g_m3']:.2f} g/m³"),
            ('Manuelle Regendämpfung',f"{link['rain_specific_db_km']:.3f} dB/km"),
            ('Manuelle Gasdämpfung',f"{link['gas_specific_db_km']:.3f} dB/km"),
            ('Regenbeeinflusster Streckenanteil',f"{link['wet_path_fraction']*100:.1f} %"),
        ])
        section(story,'ITU-R P.530 – Regenbedingte Verfügbarkeit',[
            ('Berechnung / Modellgrenze',availability_text),
            ('Einordnung','Nur regenbedingte Ausfälle; keine Gesamtverfügbarkeit. Ein Grenzwert ist kein exakter Schätzwert.'),
        ])
        story.append(Spacer(1,12))
        story.append(para('Modellgrenzen: Rechnerische Freiraum- und Geländebewertung, keine garantierte Funkverfügbarkeit. Regenrate ist ein Eingabeparameter, nicht automatisch ein nachgewiesener P.837-Abruf. P.530 wird beim PDF-Export nach Möglichkeit neu berechnet und das Ergebnis zusätzlich gespeichert (ITU-Rpy erforderlich). Geländeprofile werden beim Projektexport eingebettet und beim Import mit neuer Strecken-ID wiederhergestellt. Regenbedingte Verfügbarkeit ist nicht gleich Gesamtverfügbarkeit. Masthöhen sind alternative rechnerische Geländemodellwerte, keine Bauempfehlung. Gebäude, Vegetation, Interferenzen, Mehrwegeausbreitung, Geräte- und Stromausfälle sowie regulatorische Genehmigungen sind nicht vollständig berücksichtigt.'))
    doc=SimpleDocTemplate(str(filename),pagesize=A4,leftMargin=2.2*cm,rightMargin=2.2*cm,topMargin=2*cm,bottomMargin=2*cm)
    doc.build(story)


def terrain_drawing(profile):
    """Vector terrain plot from measured/calculated sample rows, not a schematic."""
    rows=profile['rows']
    width,height=460,205
    d=Drawing(width,height)
    left,right,bottom,top=43,450,28,178
    vals=[v for r in rows for v in (r['curved_terrain_m'],r['line_m']+r['fresnel_radius_m'],r['line_m']-r['fresnel_radius_m'])]
    low=min(vals)-5;high=max(vals)+5
    def pt(i,y):return (left+(right-left)*i/max(1,len(rows)-1),bottom+(y-low)/(high-low)*(top-bottom))
    d.add(Line(left,bottom,right,bottom,strokeColor=colors.grey,strokeWidth=.7))
    d.add(Line(left,bottom,left,top,strokeColor=colors.grey,strokeWidth=.7))
    d.add(String(2,top-2,f'{high:.0f} m',fontSize=8))
    d.add(String(2,bottom-2,f'{low:.0f} m',fontSize=8))
    d.add(String(right-70,10,f"{profile['distance_km']:.2f} km",fontSize=8))
    polygon=[]
    for i,r in enumerate(rows):polygon.extend(pt(i,r['line_m']+r['fresnel_radius_m']))
    for i in range(len(rows)-1,-1,-1):polygon.extend(pt(i,rows[i]['line_m']-rows[i]['fresnel_radius_m']))
    d.add(Polygon(polygon,fillColor=colors.HexColor('#d7e8f7'),strokeColor=None))
    for key,color,thick in [('curved_terrain_m','#398455',2),('line_m','#dc782b',1.7)]:
        coords=[]
        for i,r in enumerate(rows):coords.extend(pt(i,r[key]))
        d.add(PolyLine(coords,strokeColor=colors.HexColor(color),strokeWidth=thick))
    d.add(String(left,191,'Gruen: Gelaende inkl. Erdkruemmung',fontSize=8,fillColor=colors.HexColor('#398455')))
    d.add(String(left+205,191,'Orange: Sichtlinie | Blau: Fresnelzone',fontSize=8,fillColor=colors.HexColor('#dc782b')))
    return d

"""Generate a documented, explicitly non-validated acoustic planning report."""
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether


def create_report(filename, report, map_png=None, show_sirens=True, show_settings=True, generated_at=None):
    result=report['result']
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='RPTitle',parent=styles['Title'],fontSize=19,leading=24,textColor=colors.HexColor('#174A58'),spaceAfter=12))
    styles.add(ParagraphStyle(name='RPNote',parent=styles['Normal'],fontSize=9,leading=13,textColor=colors.HexColor('#555555')))
    story=[Paragraph('RadioPlanner | Akustischer Planungsbericht',styles['RPTitle'])]
    if generated_at:story.append(Paragraph('Erstellt am '+generated_at.strftime('%d.%m.%Y %H:%M'),styles['Normal']))
    story.extend([Spacer(1,14),Paragraph('Flächenanalyse',styles['Heading2'])])
    rows=[['Kennzahl','Ergebnis'],
          ['Untersuchungsgebiet',f"{result['area_km2']:.2f} km²"],
          ['Versorgte Fläche',f"{result['served_km2']:.2f} km² ({result['served_percent']:.1f} %)"],
          ['Unversorgte Fläche',f"{result['gap_km2']:.2f} km² ({result['gap_percent']:.1f} %)"],
          ['Mindestschallpegel',f"{report['minimum_db']:.1f} dB"]]
    table=Table(rows,colWidths=[7*cm,9*cm],hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DDECEB')),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('BOTTOMPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),9),('LINEBELOW',(0,0),(-1,-1),0.35,colors.HexColor('#CBD8D8'))]))
    story.extend([table,Spacer(1,14)])
    if report.get('comparison'):
        before=report['comparison']['before'];after=report['comparison']['after']
        story.append(Paragraph(f"Simulation: vorher {before['served_percent']:.1f} %, nachher {after['served_percent']:.1f} % (Differenz {after['served_percent']-before['served_percent']:+.1f} Prozentpunkte).",styles['Normal']))
        story.append(Spacer(1,10))
    if show_settings:
        story.append(Paragraph('Berechnungseinstellungen',styles['Heading2']))
        lat,lon=report['center'];w,h=report['area']
        fields=[('Mittelpunkt',f'{lat:.6f}°, {lon:.6f}°'),('Berechnungsgebiet',f'{w:.1f} × {h:.1f} km'),('Referenzpegel',f"{report['reference_db']:.1f} dB bei 1 m (Ersatzwert)"),('Zusätzliche Dämpfung',f"{report['attenuation']:.3f} dB/m"),('Geländeabschattung','Ja' if report['terrain'] else 'Nein'),('OSM-Gebäudeabschattung','Ja' if report['buildings'] else 'Nein'),('OSM-Datenmodus',str(report['osm_mode'])),('Polygongebiet','Ja' if report['polygon'] else 'Nein')]
        for label,value in fields:story.append(Paragraph(f'<b>{escape(label)}:</b> {escape(value)}',styles['Normal']))
        story.append(Spacer(1,12))
    if show_sirens:
        story.append(Paragraph('Berücksichtigte Sirenen',styles['Heading2']))
        for siren in report['sirens']:
            story.append(Paragraph(f"{escape(str(siren.get('name','Sirene')))} – {float(siren['latitude']):.6f}°, {float(siren['longitude']):.6f}°",styles['Normal']))
        story.append(Spacer(1,12))
    if map_png:
        story.append(Paragraph('Kartenansicht zum Exportzeitpunkt',styles['Heading2']))
        from reportlab.lib.utils import ImageReader
        width,height=ImageReader(BytesIO(map_png)).getSize()
        factor=min(16*cm/width,12*cm/height)
        story.append(Image(BytesIO(map_png),width=width*factor,height=height*factor))
        story.append(Spacer(1,12))
    if report.get('warnings'):
        story.append(Paragraph('Hinweise zur Datengrundlage',styles['Heading2']))
        for warning in report['warnings']:story.append(Paragraph(escape(str(warning)),styles['RPNote']))
    story.extend([Spacer(1,14),Paragraph('<b>Wichtiger Modellhinweis</b>',styles['Heading2']),Paragraph('Vereinfachte, rasterbasierte Näherungsrechnung. Gebäudedämpfung und Geländeeinflüsse sind modellhafte Annahmen. Keine normgerechte oder validierte Schallprognose, keine nachgewiesene Warnabdeckung. Schallbeugung, Reflexionen, Hintergrundgeräusche und meteorologische Einflüsse sind nicht vollständig berücksichtigt. OSM-Daten: © OpenStreetMap-Mitwirkende, ODbL.',styles['RPNote'])])
    doc=SimpleDocTemplate(str(filename),pagesize=(21*cm,29.7*cm),rightMargin=2.3*cm,leftMargin=2.3*cm,topMargin=2.1*cm,bottomMargin=2.1*cm)
    doc.build(story)

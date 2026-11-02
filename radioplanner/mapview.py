"""Online OpenStreetMap map; map tiles and Leaflet are loaded from the internet."""
import json
from PySide6.QtCore import QObject, Signal, Slot, QFile, QIODevice, QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

HTML = r'''<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{height:100%;margin:0}#hint{position:absolute;z-index:999;top:10px;left:50px;transform:translateX(-50%);background:white;padding:7px 12px;border-radius:6px;font:13px sans-serif;box-shadow:0 1px 5px #777}</style></head>
<body><div id="map"></div><div id="hint">Klick auf die Karte: neuen Sender anlegen</div>
<!-- Qt WebChannel JS wird aus der Qt-Ressource direkt eingebettet. -->
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
let probeMode=false, polygonMode=false, polygonPoints=[], polygonLayer=null, polygonMarkers=[], simulationMarker=null;
let bridge=null, map=null, markers=[], sirenMarkers=[], coverageLayers=[], connection=null, legend=null;
let profileMarker=null, dmrMarkers=[], dmrAreaLayer=null, dmrDbmLegend=null;
let radioLinkLayer=null;
let buildingLayer=null, buildingsVisible=true, coverageVariants=null, activeVariant="with";
let symbolSettings={sender:{shape:'tower',color:'#16803c',size:30},siren:{shape:'megaphone',color:'#ea580c',size:30},link:{shape:'dish',color:'#2563eb',size:30}};
const symbolPaths={
 tower:'M12 2 L6 22 M12 2 L18 22 M8 9 H16 M7 15 H17 M5 22 H19',
 antenna:'M12 3 V21 M6 21 H18 M8 9 A6 6 0 0 1 8 15 M16 9 A6 6 0 0 0 16 15',
 radio:'M4 8 H20 V21 H4 Z M7 12 H17 M8 4 L18 2 M8 17 H12',
 pin:'M12 22 C10 19 4 13 4 9 A8 8 0 1 1 20 9 C20 13 14 19 12 22 Z M12 6 A3 3 0 1 0 12 12 A3 3 0 1 0 12 6',
 megaphone:'M3 10 H8 L19 4 V20 L8 14 H3 Z M8 14 L10 21 H14 L12 15',
 speaker:'M4 9 H9 L15 4 V20 L9 15 H4 Z M18 9 Q22 12 18 15',
 bell:'M6 17 H18 L16 14 V10 A4 4 0 0 0 8 10 V14 Z M10 20 H14',
 dish:'M4 5 Q4 17 17 17 L20 20 M5 6 L16 17 M12 18 L10 22 H19',
 network:'M12 3 V11 M4 20 L12 11 L20 20 M2 20 H6 M18 20 H22 M10 2 H14'
};
function symbolIcon(kind){
 const v=symbolSettings[kind]||symbolSettings.sender, sz=Math.max(16,Math.min(64,Number(v.size)||30));
 if(v.custom && /^data:image\/(png|svg\+xml);base64,/.test(v.custom))return L.divIcon({className:'',html:`<img src="${v.custom}" style="width:${sz}px;height:${sz}px;object-fit:contain">`,iconSize:[sz,sz],iconAnchor:[sz/2,sz/2]});
 const path=symbolPaths[v.shape]||symbolPaths.pin;
 const html=`<svg xmlns="http://www.w3.org/2000/svg" width="${sz}" height="${sz}" viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" fill="white" fill-opacity=".9"/><path d="${path}" fill="none" stroke="${v.color||'#2563eb'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
 return L.divIcon({className:'',html:html,iconSize:[sz,sz],iconAnchor:[sz/2,sz/2]});
}
function setSymbolSettings(data){symbolSettings=data||symbolSettings; if(map){setStations(lastStations);setSirens(lastSirens);}}
let lastStations=[],lastSirens=[];
if (typeof QWebChannel !== 'undefined' && typeof qt !== 'undefined' && qt.webChannelTransport) {
 new QWebChannel(qt.webChannelTransport, function(ch){bridge=ch.objects.bridge;});
} else {
 console.warn('QWebChannel nicht verfügbar: Kartenklick-Übernahme deaktiviert.');
}
function initialize(){
 if(typeof L==='undefined'){document.getElementById('hint').textContent='Karte benötigt Internetverbindung (Leaflet/CDN).';return;}
 map=L.map('map').setView([49.32,7.0],9);
 L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
 map.on('click', e=>{if(polygonMode){addPolygonPoint(e.latlng.lat,e.latlng.lng);return;}if(bridge){if(probeMode)bridge.probeClicked(e.latlng.lat,e.latlng.lng);else bridge.mapClicked(e.latlng.lat,e.latlng.lng)}});
}
function addPolygonPoint(lat,lon){
 if(!polygonMode)return;
 polygonPoints.push([lat,lon]);drawPolygon();
 setHint('Polygon: '+polygonPoints.length+' Eckpunkt(e) – mindestens 3 setzen, dann „Polygon übernehmen“');
}
function drawPolygon(){
 if(!map)return;
 if(polygonLayer)map.removeLayer(polygonLayer);
 polygonMarkers.forEach(m=>map.removeLayer(m));polygonMarkers=[];
 polygonLayer=null;
 polygonPoints.forEach((p,i)=>{
  const marker=L.circleMarker(p,{radius:6,color:'#581c87',weight:2,fillColor:'#facc15',fillOpacity:1,interactive:false}).addTo(map);
  polygonMarkers.push(marker);
 });
 if(polygonPoints.length>=2){
  polygonLayer=(polygonPoints.length>=3?L.polygon(polygonPoints,{color:'#7c3aed',weight:3,fillOpacity:.08,interactive:false}):L.polyline(polygonPoints,{color:'#7c3aed',weight:3,interactive:false})).addTo(map);
 }
}
function startPolygon(){polygonMode=true;probeMode=false;polygonPoints=[];drawPolygon();if(map)map.getContainer().style.cursor='crosshair';setHint('Polygon: Eckpunkte anklicken, anschließend „Polygon übernehmen“');}
function finishPolygon(){
 if(polygonPoints.length<3){alert('Bitte mindestens drei Eckpunkte wählen.');return;}
 if(!bridge){alert('Kartenverbindung noch nicht bereit. Bitte erneut versuchen.');return;}
 polygonMode=false;if(map)map.getContainer().style.cursor='';
 bridge.polygonFinished(JSON.stringify(polygonPoints));
}
function clearPolygon(){polygonMode=false;polygonPoints=[];drawPolygon();if(map)map.getContainer().style.cursor='';}
function showSimulation(lat,lon){if(simulationMarker&&map)map.removeLayer(simulationMarker);if(map)simulationMarker=L.circleMarker([lat,lon],{radius:11,color:'#7c3aed',weight:3,fillColor:'#c4b5fd',fillOpacity:.8}).addTo(map).bindPopup('Simulierter Sirenenstandort (nicht gespeichert)');}
function clearSimulation(){if(simulationMarker&&map)map.removeLayer(simulationMarker);simulationMarker=null;}
function showProfilePosition(lat,lon){
 if(!map)return;
 if(!profileMarker)profileMarker=L.circleMarker([lat,lon],{radius:9,color:'#c62828',weight:3,fillColor:'#ffeb3b',fillOpacity:.9,interactive:false}).addTo(map);
 else profileMarker.setLatLng([lat,lon]);
}
function clearProfilePosition(){if(profileMarker&&map)map.removeLayer(profileMarker);profileMarker=null;}
function setProbeMode(enabled){probeMode=!!enabled; if(map)map.getContainer().style.cursor=probeMode?'crosshair':'';}
function setHint(message){document.getElementById('hint').textContent=message;}
function setStations(stations){
 lastStations=stations; if(!map)return;
 markers.forEach(m=>map.removeLayer(m));markers=[];
 stations.forEach(s=>{const m=L.marker([s.latitude,s.longitude],{icon:symbolIcon("sender")}).addTo(map);
 m.bindPopup('<b>'+escapeHtml(s.name)+'</b><br>'+s.frequency_mhz+' MHz');
 m.on('click',()=>{if(bridge)bridge.stationClicked(s.id)});markers.push(m);});
 if(markers.length)map.fitBounds(L.featureGroup(markers).getBounds().pad(0.2),{maxZoom:13});
}
function setDmrMarkers(items){
 if(!map)return;
 dmrMarkers.forEach(m=>map.removeLayer(m));dmrMarkers=[];
 (items||[]).forEach(s=>{
  const isRepeater=s.kind==='repeater', vehicle=s.kind==='vehicle', fixed=s.kind==='fixed';
  const color=isRepeater?'#1d4ed8':vehicle?'#b45309':fixed?'#7c3aed':'#15803d';
  // Distinct, legible silhouettes: car with roof antenna; fixed station with house and aerial.
  const path=isRepeater?'M12 2 L6 22 M12 2 L18 22 M8 9 H16 M7 15 H17 M5 22 H19':
    vehicle?'M3 14 L5.5 9.5 Q6 8.5 7.5 8.5 H16.5 Q18 8.5 18.5 9.5 L21 14 V19 H19.5 M4.5 19 H3 V14 Z M7 19 H17 M21 14 H3 M7 8.5 L9 14 M17 8.5 L15 14 M12 8.5 V3 M12 3 L15 1.5 M5.5 19 A2 2 0 1 0 9.5 19 A2 2 0 1 0 5.5 19 M14.5 19 A2 2 0 1 0 18.5 19 A2 2 0 1 0 14.5 19':
    fixed?'M3 12 L12 5 L21 12 M5 11 V22 H19 V11 M9 22 V16 H15 V22 M12 5 V1 M12 1 L16 3':
    'M9 2 V7 M7 7 H17 V22 H7 Z M9 10 H15 V14 H9 Z M9 17 H10 M12 17 H13 M15 17 H16 M9 19 H10 M12 19 H13 M15 19 H16';
  const html=`<svg xmlns="http://www.w3.org/2000/svg" width="38" height="42" viewBox="0 0 24 26"><rect x="0" y="0" width="24" height="26" rx="5" fill="white" fill-opacity=".95"/><path d="${path}" fill="none" stroke="${color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const icon=L.divIcon({className:'',html,iconSize:[34,40],iconAnchor:[17,20]});
  const m=L.marker([s.latitude,s.longitude],{icon}).addTo(map);
  m.bindPopup('<b>'+escapeHtml(s.name)+'</b><br>'+ (isRepeater?'DMR-Repeater':vehicle?'DMR-Fahrzeugfunkgerät':fixed?'DMR-Feststation':'DMR-Handfunkgerät')+'<br><small>Bearbeiten im Menü DMR Tier II</small>');
  m.on('click',()=>{if(bridge)bridge.dmrClicked(s.kind,s.id)});
  dmrMarkers.push(m);
 });
}
function setDmrAreaCoverage(cells, coverageOnly=false, paletteBands=null){
 if(dmrDbmLegend){map.removeControl(dmrDbmLegend);dmrDbmLegend=null;}
 if(!map)return;
 if(dmrAreaLayer)map.removeLayer(dmrAreaLayer);
 dmrAreaLayer=null;
 if(!cells||!cells.length)return;
 // Build a single georeferenced raster overlay instead of thousands of Leaflet
 // rectangles. Every pixel represents one actually computed cell, not a
 // fabricated intermediate terrain sample.
 const south=Math.min(...cells.map(c=>c.south)),north=Math.max(...cells.map(c=>c.north));
 const west=Math.min(...cells.map(c=>c.west)),east=Math.max(...cells.map(c=>c.east));
 const dx=cells[0].east-cells[0].west,dy=cells[0].north-cells[0].south;
 const cols=Math.max(1,Math.round((east-west)/dx)),rows=Math.max(1,Math.round((north-south)/dy));
 if(cols*rows>1000000)return;
 const canvas=document.createElement('canvas');canvas.width=cols;canvas.height=rows;
 const ctx=canvas.getContext('2d'),img=ctx.createImageData(cols,rows);
 const palette=[[220,38,38],[249,115,22],[234,179,8],[134,199,106],[21,128,61]];
 function parseHex(hex){return [parseInt(hex.slice(1,3),16),parseInt(hex.slice(3,5),16),parseInt(hex.slice(5,7),16)];}
 if(paletteBands && paletteBands.length===8){
  dmrDbmLegend=L.control({position:'bottomright'});
  dmrDbmLegend.onAdd=function(){
   const box=L.DomUtil.create('div');box.style.cssText='background:white;padding:10px 12px;border-radius:6px;box-shadow:0 1px 7px #777;font:12px sans-serif;line-height:1.6;max-height:50vh;overflow:auto';
   const title=document.createElement('strong');title.textContent='Downlink-Empfangspegel (dBm)';box.appendChild(title);
   paletteBands.forEach((band,i)=>{
    const row=document.createElement('div');row.style.whiteSpace='nowrap';
    const swatch=document.createElement('span');swatch.style.cssText='display:inline-block;width:14px;height:12px;margin-right:7px;vertical-align:middle;background:'+band.color;
    row.appendChild(swatch);
    const text=document.createElement('span');text.textContent=(i===0?'≥ '+band.min:band.min+' bis < '+paletteBands[i-1].min)+' dBm · '+band.label;row.appendChild(text);box.appendChild(row);
   });
   const footer=document.createElement('div');footer.textContent='< '+paletteBands[7].min+' dBm: transparent';footer.style.marginTop='4px';box.appendChild(footer);
   L.DomEvent.disableClickPropagation(box);return box;
  };dmrDbmLegend.addTo(map);
 }
 for(const c of cells){
  if(coverageOnly && c.status==='poor')continue;
  const x=Math.round((c.west-west)/dx),y=Math.round((north-c.north)/dy);
  if(x<0||x>=cols||y<0||y>=rows)continue;
  if(c.band===null || c.band===undefined)continue;
  const color=paletteBands && paletteBands.length===8 ? parseHex(paletteBands[c.band].color) : palette[Math.max(0,Math.min(4,Number(c.band)||0))];
  const k=4*(y*cols+x);
  img.data[k]=color[0];img.data[k+1]=color[1];img.data[k+2]=color[2];img.data[k+3]=coverageOnly?155:115;
 }
 ctx.putImageData(img,0,0);
 dmrAreaLayer=L.imageOverlay(canvas.toDataURL('image/png'),[[south,west],[north,east]],{interactive:false,opacity:1}).addTo(map);
}
function setRadioLinks(links){
 if(!map)return;
 if(radioLinkLayer)map.removeLayer(radioLinkLayer);
 radioLinkLayer=L.layerGroup();
 const colors={green:'#188038',yellow:'#e0a000',red:'#c62828',unknown:'#64748b'};
 (links||[]).forEach(link=>{
  if(!link.a||!link.b)return;
  const color=colors[link.status]||colors.unknown;
  const line=L.polyline([link.a,link.b],{color:color,weight:4,opacity:.85}).addTo(radioLinkLayer);
  const value=link.margin===null?'nicht bewertet':Number(link.margin).toFixed(1)+' dB';
  line.bindPopup('<b>'+escapeHtml(link.name||'Richtfunk')+'</b><br>Fadingreserve: '+escapeHtml(value)+'<br><small>Freiraummodell, Zielreserve 20 dB</small>');
 });
 radioLinkLayer.addTo(map);
}
function setSirens(sirens){
 lastSirens=sirens; if(!map)return;
 sirenMarkers.forEach(m=>map.removeLayer(m));sirenMarkers=[];
 sirens.forEach(s=>{
  const marker=L.marker([s.latitude,s.longitude],{icon:symbolIcon("siren")}).addTo(map);
  marker.bindPopup('<b>🔊 '+escapeHtml(s.name)+'</b><br>'+escapeHtml(s.manufacturer)+'<br>'+(s.power_w===null?'E57':s.power_w+' W'));
  sirenMarkers.push(marker);
 });
}
function destination(lat,lon,bearing,distance){
 const R=6371008.8,rad=Math.PI/180,phi=lat*rad,lambda=lon*rad,theta=bearing*rad,d=distance/R;
 const p=Math.asin(Math.sin(phi)*Math.cos(d)+Math.cos(phi)*Math.sin(d)*Math.cos(theta));
 const l=lambda+Math.atan2(Math.sin(theta)*Math.sin(d)*Math.cos(phi),Math.cos(d)-Math.sin(phi)*Math.sin(p));
 return [p/rad,((l/rad+540)%360)-180];
}
function sectorPoints(s,bearing,opening,radius){
 const points=[[s.latitude,s.longitude]],steps=Math.max(8,Math.ceil(opening/4));
 for(let i=0;i<=steps;i++)points.push(destination(s.latitude,s.longitude,bearing-opening/2+opening*i/steps,radius));
 return points;
}
function setBuildings(items){
 if(!map)return;
 if(buildingLayer)map.removeLayer(buildingLayer);
 buildingLayer=L.layerGroup();
 (items||[]).forEach(b=>{
  if(!Array.isArray(b.polygon)||b.polygon.length<3)return;
  L.polygon(b.polygon,{color:'#48576b',weight:1,fillColor:'#64748b',fillOpacity:0.22,interactive:false}).addTo(buildingLayer);
 });
 if(buildingsVisible)buildingLayer.addTo(map);
}
function toggleBuildings(enabled){
 buildingsVisible=!!enabled;
 if(!map||!buildingLayer)return;
 if(buildingsVisible)buildingLayer.addTo(map);else map.removeLayer(buildingLayer);
}
function setCoverageComparison(variants){
 coverageVariants=variants;
 activeVariant='with';
 setCoverage(variants ? variants.with : null);
}
function selectCoverageVariant(name){
 if(!coverageVariants||!coverageVariants[name])return;
 activeVariant=name;setCoverage(coverageVariants[name]);
}
function setCoverage(item){
 if(!map)return;
 coverageLayers.forEach(x=>map.removeLayer(x));coverageLayers=[];
 if(legend){map.removeControl(legend);legend=null;}
 if(!item || !item.image || !item.bounds)return;
 const layer=L.imageOverlay(item.image,item.bounds,{opacity:0.86,interactive:false}).addTo(map);
 coverageLayers.push(layer);
 legend=L.control({position:'bottomright'});
 legend.onAdd=function(){
  const div=L.DomUtil.create('div');
  div.style.cssText='background:white;padding:10px 12px;border-radius:6px;box-shadow:0 1px 6px #777;font:12px sans-serif;line-height:1.8';
  const t=Number(item.threshold);
  if(item.analysis_mode==='difference'){
   div.innerHTML='<b>Gebäudedämpfung (Näherung)</b><br><span style="color:#ffda5a">■</span> 1–5 dB<br><span style="color:#f98b2c">■</span> 5–10 dB<br><span style="color:#dc4230">■</span> 10–15 dB<br><span style="color:#801b62">■</span> ≥15 dB<br><small>Transparent: unter 1 dB</small>';return div;
  }
  if(item.analysis_mode==='gaps'){
   div.innerHTML='<b>Versorgungsanalyse (Näherung)</b><br><span style="color:#23a04b">■</span> Mindestpegel erreicht<br><span style="color:#dc3237">■</span> Unter '+t+' dB<br><small>Nur im Untersuchungsgebiet</small>';
   return div;
  }
  const bins=[['#206ed7',t+'–'+(t+5)],['#19afb9',(t+5)+'–'+(t+10)],['#41b45a',(t+10)+'–'+(t+15)],['#f5be28',(t+15)+'–'+(t+20)],['#e1412d','≥ '+(t+20)]];
  div.innerHTML='<b>Schallpegel (dB, Näherung)</b><br>'+bins.map(b=>'<span style="display:inline-block;width:13px;height:11px;background:'+b[0]+';margin-right:6px"></span>'+b[1]+' dB').join('<br>')+'<br><small>Transparent: unter '+t+' dB</small>';
  return div;
 };
 if(Number.isFinite(Number(item.threshold)))legend.addTo(map);
 map.fitBounds(item.bounds,{maxZoom:13});
}
function showConnection(a,b){
 if(!map)return;
 if(connection)map.removeLayer(connection);
 connection=L.polyline([[a.latitude,a.longitude],[b.latitude,b.longitude]],{color:'#e57a24',weight:4}).addTo(map);
 map.fitBounds(connection.getBounds().pad(0.3));
}
function escapeHtml(t){return String(t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
window.addEventListener('load',initialize);
</script></body></html>'''

class MapBridge(QObject):
    coordinates = Signal(float,float)
    station_selected = Signal(int)
    dmr_selected = Signal(str,int)
    probe_requested = Signal(float,float)
    polygon_ready = Signal(str)
    @Slot(str)
    def polygonFinished(self, payload): self.polygon_ready.emit(payload)
    @Slot(float,float)
    def mapClicked(self, lat,lon): self.coordinates.emit(lat,lon)
    @Slot(float,float)
    def probeClicked(self,lat,lon): self.probe_requested.emit(lat,lon)
    @Slot(int)
    def stationClicked(self, station_id): self.station_selected.emit(station_id)
    @Slot(str,int)
    def dmrClicked(self, kind, marker_id): self.dmr_selected.emit(kind, marker_id)

class MapView(QWebEngineView):
    def __init__(self):
        super().__init__()
        self.bridge = MapBridge()
        self.channel = QWebChannel(self.page())
        self.channel.registerObject('bridge',self.bridge)
        self.page().setWebChannel(self.channel)
        # Die WebChannel-Bibliothek aus Qt laden und vor dem eigenen JS einbetten.
        # Das umgeht Ladeprobleme mit dem qrc://-Script-Tag in setHtml().
        html = HTML
        resource = QFile(":/qtwebchannel/qwebchannel.js")
        if resource.open(QIODevice.OpenModeFlag.ReadOnly):
            channel_js = bytes(resource.readAll()).decode("utf-8")
            resource.close()
            html = html.replace('<!-- Qt WebChannel JS wird aus der Qt-Ressource direkt eingebettet. -->',
                                '<script>' + channel_js + '</script>')
        else:
            print('WARNUNG: Qt-WebChannel-JavaScript-Ressource nicht verfügbar.')
        self.loadFinished.connect(lambda ok: self.refresh())
        self.setHtml(html, QUrl('https://radioplanner.local/'))
        self._stations = []
        self._sirens = []
        self._coverage = {}
        self._buildings = []
        self._comparison = None
        self._radio_links = []
        self._dmr_markers = []
        self._dmr_area = []

    def start_polygon(self):
        self.page().runJavaScript('if(typeof startPolygon === \"function\") startPolygon();')

    def add_polygon_point(self, lat, lon):
        self.page().runJavaScript(f'if(typeof addPolygonPoint === \"function\") addPolygonPoint({float(lat)},{float(lon)});')

    def finish_polygon(self):
        self.page().runJavaScript('finishPolygon();')

    def show_simulation(self, lat, lon):
        self.page().runJavaScript(f'showSimulation({float(lat)},{float(lon)});')

    def clear_simulation(self):
        self.page().runJavaScript('clearSimulation();')

    def show_profile_position(self,lat,lon):
        self.page().runJavaScript(f'if(typeof showProfilePosition === "function") showProfilePosition({float(lat)},{float(lon)});')

    def clear_profile_position(self):
        self.page().runJavaScript('if(typeof clearProfilePosition === "function") clearProfilePosition();')

    def set_dmr_markers(self, markers):
        self._dmr_markers = list(markers)
        payload=json.dumps(self._dmr_markers,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setDmrMarkers === "function") setDmrMarkers({payload});')

    def set_dmr_area_coverage(self, cells, coverage_only=False, palette_bands=None):
        self._dmr_area = list(cells)
        payload=json.dumps(self._dmr_area,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setDmrAreaCoverage === "function") setDmrAreaCoverage({payload}, {json.dumps(bool(coverage_only))}, {json.dumps(palette_bands)});')

    def set_stations(self, stations):
        self._stations = [dict(s) for s in stations]
        self.refresh()

    def set_radio_links(self, links):
        self._radio_links = list(links)
        payload=json.dumps(self._radio_links,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setRadioLinks === "function") setRadioLinks({payload});')

    def set_sirens(self, sirens):
        self._sirens = [dict(s) for s in sirens]
        self.refresh()

    def set_coverage(self, coverage):
        self._coverage = coverage
        self._comparison = None
        payload=json.dumps(coverage,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setCoverage === "function") setCoverage({payload});')

    def set_buildings(self, buildings):
        self._buildings = list(buildings)
        payload=json.dumps(self._buildings,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setBuildings === "function") setBuildings({payload});')

    def toggle_buildings(self, enabled):
        self.page().runJavaScript('toggleBuildings('+json.dumps(bool(enabled))+');')

    def set_coverage_comparison(self, variants):
        self._comparison=variants
        self._coverage=variants['with']
        payload=json.dumps(variants,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setCoverageComparison === "function") setCoverageComparison({payload});')

    def select_coverage_variant(self, name):
        self.page().runJavaScript('selectCoverageVariant('+json.dumps(name)+');')

    def set_probe_mode(self, enabled):
        self.page().runJavaScript("if(typeof setProbeMode === 'function') setProbeMode("+json.dumps(bool(enabled))+");")

    def set_hint(self, message):
        self.page().runJavaScript("if(typeof setHint === 'function') setHint("+json.dumps(message,ensure_ascii=False)+");")

    def show_connection(self, a, b):
        pa=json.dumps(dict(latitude=a["latitude"],longitude=a["longitude"]))
        pb=json.dumps(dict(latitude=b["latitude"],longitude=b["longitude"]))
        self.page().runJavaScript(f"if(typeof showConnection === 'function') showConnection({pa},{pb});")

    def apply_symbol_settings(self, settings):
        self._symbol_settings = settings
        self.page().runJavaScript('if(typeof setSymbolSettings === "function") setSymbolSettings('+json.dumps(settings,ensure_ascii=False)+');')

    def refresh(self):
        if hasattr(self, '_symbol_settings'):
            self.page().runJavaScript('if(typeof setSymbolSettings === "function") setSymbolSettings('+json.dumps(self._symbol_settings,ensure_ascii=False)+');')
        payload = json.dumps(self._stations, ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setStations === "function") setStations({payload});')
        dmr=json.dumps(self._dmr_markers,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setDmrMarkers === "function") setDmrMarkers({dmr});')
        links=json.dumps(self._radio_links,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setRadioLinks === "function") setRadioLinks({links});')
        sirens=json.dumps(self._sirens,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setSirens === "function") setSirens({sirens});')
        coverage=json.dumps(self._coverage,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setCoverage === "function") setCoverage({coverage});')
        buildings=json.dumps(self._buildings,ensure_ascii=False)
        self.page().runJavaScript(f'if(typeof setBuildings === "function") setBuildings({buildings});')
        if self._comparison:
            variants=json.dumps(self._comparison,ensure_ascii=False)
            self.page().runJavaScript(f'if(typeof setCoverageComparison === "function") setCoverageComparison({variants});')


from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter,QPen,QColor,QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QWidget

class ProfilePlot(QWidget):
    positionChanged = Signal(int)
    def __init__(self, result, parent=None):
        super().__init__(parent);self.result=result;self.setMinimumSize(580,250);self.selected_index=None;self.setMouseTracking(True)
    def select_index(self,index):
        self.selected_index=max(0,min(len(self.result["rows"])-1,int(index)))
        self.update()

    def mouseMoveEvent(self,event):
        rows=self.result["rows"]
        left,right=62,self.width()-25
        index=round((event.position().x()-left)/max(1,right-left)*(len(rows)-1))
        self.select_index(index)
        self.positionChanged.emit(self.selected_index)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        rows=self.result['rows'];left,top,right,bottom=62,25,self.width()-25,self.height()-46
        all_heights=[v for r in rows for v in (r['curved_terrain_m'],r['line_m']+r['fresnel_radius_m'],r['line_m']-r['fresnel_radius_m'])]
        lo=min(all_heights)-5;hi=max(all_heights)+5
        def pt(i,v):return QPointF(left+(right-left)*i/(len(rows)-1),bottom-(v-lo)/(hi-lo)*(bottom-top))
        p.setPen(QPen(QColor('#999999'),1));p.drawLine(left,bottom,right,bottom);p.drawLine(left,top,left,bottom)
        p.drawText(5,18,f'{hi:.0f} m');p.drawText(5,bottom,f'{lo:.0f} m')
        p.drawText(right-100,self.height()-10,f"{self.result['distance_km']:.2f} km")
        def line(values,color,width=2):
            p.setPen(QPen(QColor(color),width))
            for i in range(1,len(values)):p.drawLine(pt(i-1,values[i-1]),pt(i,values[i]))
        upper=[r['line_m']+r['fresnel_radius_m'] for r in rows]
        lower=[r['line_m']-r['fresnel_radius_m'] for r in rows]
        polygon=QPolygonF([pt(i,v) for i,v in enumerate(upper)]+[pt(i,v) for i,v in reversed(list(enumerate(lower)))])
        p.setPen(Qt.NoPen);p.setBrush(QColor(80,155,220,65));p.drawPolygon(polygon);p.setBrush(Qt.NoBrush)
        line([r['curved_terrain_m'] for r in rows],'#398455',3)
        line([r['line_m'] for r in rows],'#dc782b',2)
        p.setPen(QColor('#398455'));p.drawText(left+10,15,'Grün: Gelände inkl. Erdkrümmung')
        p.setPen(QColor('#dc782b'));p.drawText(left+270,15,'Orange: Sichtlinie | Blau: 1. Fresnelzone')
        if self.selected_index is not None:
            i=self.selected_index
            x=left+(right-left)*i/(len(rows)-1)
            p.setPen(QPen(QColor('#c62828'),2,Qt.DashLine))
            p.drawLine(QPointF(x,top),QPointF(x,bottom))
            p.setBrush(QColor('#c62828'))
            p.drawEllipse(pt(i,rows[i]['curved_terrain_m']),5,5)
        p.end()

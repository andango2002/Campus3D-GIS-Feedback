# -*- coding: utf-8 -*-
"""Compact dual-axis-free A/B MRT route plot: same y-axis in Celsius."""
import math
from qgis.PyQt.QtCore import Qt,QRectF
from qgis.PyQt.QtGui import QColor,QPen,QPainter
from qgis.PyQt.QtWidgets import QWidget,QSizePolicy


class ABRoutePlot(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.rows=[]
        self.setMinimumHeight(240);self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        self.setToolTip('A（青）・B（橙）を共通温度軸で表示。非対応区間は線をつなぎません。')

    def set_rows(self,rows):
        self.rows=list(rows);self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor('white'))
        w,h=self.width(),self.height();left,right,top,bottom=52,15,34,40
        pw=max(1,w-left-right);ph=max(1,h-top-bottom)
        p.setPen(QColor('#27333b'));p.drawText(left,17,'MRT [°C]')
        if len(self.rows)<2:
            p.drawText(self.rect(),Qt.AlignCenter,'経路比較でA/BのMRTを重ねて表示します');p.end();return
        total=float(self.rows[-1]['distance_m']);values=[float(row[key]) for row in self.rows
            for key in ('a_c','b_c') if row.get(key) is not None and math.isfinite(float(row[key]))]
        lo,hi=(min(values),max(values)) if values else (20.,50.)
        pad=max(1.,(hi-lo)*.12);lo-=pad;hi+=pad
        def xy(d,t):return left+d/max(total,1e-8)*pw,top+ph-(t-lo)/(hi-lo)*ph
        p.setPen(QPen(QColor('#e0e5e9'),1))
        for i in range(5):
            v=lo+(hi-lo)*i/4;y=xy(0,v)[1]
            p.drawLine(left,round(y),left+pw,round(y));p.setPen(QColor('#344451'))
            p.drawText(QRectF(0,y-9,left-7,18),Qt.AlignRight|Qt.AlignVCenter,f'{v:.1f}')
            p.setPen(QPen(QColor('#e0e5e9'),1))
            x=left+pw*i/4;p.drawLine(round(x),top,round(x),top+ph)
            p.setPen(QColor('#344451'))
            p.drawText(QRectF(x-28,top+ph+3,56,17),Qt.AlignHCenter,f'{total*i/4:.0f}')
            p.setPen(QPen(QColor('#e0e5e9'),1))
        p.setPen(QColor('#27333b'));p.drawText(QRectF(left,top+ph+21,pw,15),Qt.AlignCenter,'経路距離 [m]')
        for key,color in (('a_c','#2466b6'),('b_c','#db7531')):
            pen=QPen(QColor(color),2);p.setPen(pen);prev=None
            for row in self.rows:
                value=row.get(key)
                if value is None:prev=None;continue
                now=xy(float(row['distance_m']),float(value))
                if prev is not None:p.drawLine(round(prev[0]),round(prev[1]),round(now[0]),round(now[1]))
                prev=now
            p.setPen(QColor(color));p.drawText(left+(0 if key=='a_c' else 100),28,
                                               'A 基準' if key=='a_c' else 'B 比較')
        if not values:
            p.setPen(QColor('#b72d2b'))
            p.drawText(QRectF(left,top,pw,ph),Qt.AlignCenter,'対応するMRT評価点なし')
        p.end()

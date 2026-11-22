# -*- coding: utf-8 -*-
"""Small no-dependency Qt MRT route plot for the dock panel."""
import math
from qgis.PyQt.QtCore import Qt, QRectF
from qgis.PyQt.QtGui import QPainter, QColor, QPen, QFont
from qgis.PyQt.QtWidgets import QWidget, QSizePolicy


class MrtRoutePlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip('横軸：経路上の距離(m)、縦軸：最近傍の計算済みMRT点(℃)。欠測を跨いで線を結びません。')

    def set_rows(self, rows):
        self.rows = list(rows)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor('#ffffff'))
        w, h = self.width(), self.height()
        l, r, top, bottom = 52, 16, 28, 36
        pw, ph = max(1,w-l-r), max(1,h-top-bottom)
        p.setPen(QColor('#27333b'))
        p.drawText(l, 17, 'MRT [°C]')
        if len(self.rows) < 2:
            p.drawText(self.rect(), Qt.AlignCenter, '経路を指定するとMRTプロファイルを表示します')
            p.end()
            return
        total = float(self.rows[-1]['distance_m'])
        points = [(float(row['distance_m']), float(row['mrt_c'])) for row in self.rows
                  if row.get('mrt_c') is not None and math.isfinite(float(row['mrt_c']))]
        if points:
            lo = min(t for _,t in points)
            hi = max(t for _,t in points)
            pad = max(1.0, (hi-lo)*0.12)
            lo -= pad
            hi += pad
        else:
            lo, hi = 20, 50
        def pixel(d, t):
            return l + (d/total)*pw, top+ph-(t-lo)/(hi-lo)*ph
        p.setPen(QPen(QColor('#d7dce0'),1))
        for i in range(5):
            t = lo+(hi-lo)*i/4
            y = pixel(0,t)[1]
            p.drawLine(l,round(y),l+pw,round(y))
            p.setPen(QColor('#344451'))
            p.drawText(QRectF(0,y-9,l-7,18),Qt.AlignRight|Qt.AlignVCenter,f'{t:.1f}')
            p.setPen(QPen(QColor('#d7dce0'),1))
        for i in range(5):
            d = total*i/4
            x = l+pw*i/4
            p.drawLine(round(x),top,round(x),top+ph)
            p.setPen(QColor('#344451'))
            p.drawText(QRectF(x-26,top+ph+2,52,18),Qt.AlignHCenter|Qt.AlignTop,f'{d:.0f}')
            p.setPen(QPen(QColor('#d7dce0'),1))
        p.setPen(QColor('#27333b'))
        p.drawText(QRectF(l,top+ph+18,pw,15),Qt.AlignCenter, '経路距離 [m]')
        prev = None
        p.setPen(QPen(QColor('#2259ab'),2))
        for row in self.rows:
            val = row.get('mrt_c')
            if val is None:
                prev = None
                continue
            at = pixel(float(row['distance_m']),float(val))
            if prev is not None:
                p.drawLine(round(prev[0]),round(prev[1]),round(at[0]),round(at[1]))
            prev = at
        p.setBrush(QColor('#2259ab'))
        p.setPen(QPen(QColor('#2259ab'),1))
        for d,t in points:
            x,y = pixel(d,t)
            p.drawEllipse(round(x)-2,round(y)-2,4,4)
        if not points:
            p.setPen(QColor('#b72d2b'))
            p.drawText(QRectF(l,top,pw,ph), Qt.AlignCenter, '有効MRTサンプルなし（欠測）')
        p.end()

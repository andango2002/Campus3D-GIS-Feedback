# -*- coding: utf-8 -*-
"""QGIS map-canvas-only thermal colorbar (no layer-data mutations).

Position and size are configurable. Export uses a dedicated screenshot of the
QGIS map canvas with the overlay composited once, without changing data.
Legend reflects actually applied renderer classes, not draft spinbox values.
"""
from datetime import datetime, timedelta

from qgis.PyQt.QtCore import QEvent, QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen
from qgis.PyQt.QtWidgets import QWidget

BAR_WIDTH = 348
BAR_HEIGHT = 121
BAR_HEIGHT_WITHOUT_MISSING_NOTE = 95
BAR_POSITIONS = ('top_right', 'top_left', 'bottom_right', 'bottom_left')
BAR_SCALES = {'compact': 0.8, 'normal': 1.0, 'large': 1.25}


def timestamp_label(iso_text):
    s = str(iso_text or '').strip()
    if not s:
        return '解析時刻：不明'
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return dt.strftime('%Y-%m-%d %H:%M') + ' (時区分不明)'
        offset = dt.utcoffset()
        suffix = ' JST' if offset == timedelta(hours=9) else dt.strftime(' %z')
        return dt.strftime('%Y-%m-%d %H:%M') + suffix
    except (ValueError, TypeError, OverflowError):
        return s[:42]


def same_renderer_classes(layer_groups, temperature_field):
    """Return (actual QGIS renderer classes, warning or None).

    Checks only *visible* layers supplied by the caller. For Surface, multiple
    visible GROUND/ROOF/WALL layers must share exactly the same classes.
    """
    if not layer_groups:
        return None, '温度レイヤがありません'
    reference = None
    for layer in layer_groups:
        if not layer.isValid():
            return None, '無効なレイヤがあります'
        renderer = layer.renderer()
        if renderer is None or renderer.type() != 'graduatedSymbol':
            return None, '温度階級を表示するには「温度表示を適用」してください'
        if renderer.classAttribute() != temperature_field:
            return None, '温度以外の属性で色分けされています'
        current = []
        for group in renderer.ranges():
            sym = group.symbol()
            if sym is None:
                return None, '温度階級の色を読み取れません'
            current.append((float(group.lowerValue()),
                            float(group.upperValue()), sym.color().name().lower()))
        if not current:
            return None, '温度階級がありません'
        if reference is None:
            reference = current
        else:
            if len(reference) != len(current):
                return None, '表示中の温度レイヤ間で階級が異なります（再適用してください）'
            for (a, b, col), (aa, bb, coll) in zip(reference, current):
                if abs(a - aa) > .02 or abs(b - bb) > .02 or col != coll:
                    return None, '表示中の温度レイヤ間で階級が異なります（再適用してください）'
    for prev, nxt in zip(reference, reference[1:]):
        if abs(prev[1] - nxt[0]) > .02:
            return None, '階級に隙間・重複があります（再適用してください）'
    return reference, None


def scale_ticks(cuts):
    return ([cuts[0][0]] + [r[1] for r in cuts]) if cuts else []


class MapTemperatureColorbar(QWidget):
    """Transparent, noninteractive overlay on the map canvas."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._cuts = []
        self._kind = 'surface'
        self._timestamp = ''
        self._prefix = ''
        self._position = 'top_right'
        self._size_choice = 'normal'
        self._show_missing_note = True
        self._effective_scale = 1.0
        self.setObjectName('Campus3D_Thermal_MapColorbar')
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(BAR_WIDTH, BAR_HEIGHT)
        canvas.installEventFilter(self)
        self._position_overlay()
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self._canvas and event.type() in (QEvent.Resize, QEvent.Show):
            self._position_overlay()
        return False

    def configure(self, position, size_choice):
        if position not in BAR_POSITIONS:
            position = 'top_right'
        if size_choice not in BAR_SCALES:
            size_choice = 'normal'
        self._position = position
        self._size_choice = size_choice
        self._position_overlay()
        self.update()

    def set_show_missing_note(self, enabled):
        """Include an explicit *explanatory* legend note, not a measured NoData mask."""
        self._show_missing_note = bool(enabled)
        self._position_overlay()
        self.update()

    def _base_height(self):
        return BAR_HEIGHT if self._show_missing_note else BAR_HEIGHT_WITHOUT_MISSING_NOTE

    def _position_overlay(self):
        # Avoid overrun when the QGIS canvas is narrowed by the side dock.
        aw = max(1, self._canvas.width() - 16)
        ah = max(1, self._canvas.height() - 16)
        base_h = self._base_height()
        self._effective_scale = min(BAR_SCALES[self._size_choice],
                                    aw / BAR_WIDTH, ah / base_h)
        w = max(1, int(round(BAR_WIDTH * self._effective_scale)))
        h = max(1, int(round(base_h * self._effective_scale)))
        self.setFixedSize(w, h)
        x = 8 if self._position.endswith('left') else max(0, self._canvas.width() - w - 8)
        y = 8 if self._position.startswith('top') else max(0, self._canvas.height() - h - 8)
        self.move(x, y)
        self.raise_()

    def set_legend(self, kind, timestamp, cuts, prefix="", subtitle=None):
        self._kind = kind
        self._prefix = prefix
        self._timestamp = str(subtitle) if subtitle is not None else timestamp_label(timestamp)
        self._cuts = list(cuts)
        self._position_overlay()
        self.update()
        self.show()
        self.raise_()

    def clear(self):
        self._cuts = []
        self.hide()

    def export_canvas_png(self, destination):
        """Capture the *current visible canvas* and explicitly composite our legend.

        QGIS/PyQt QWidget.grab includes child widgets by default.  To avoid an
        overlay being captured twice, hide it while taking the map screenshot,
        then composite the separately captured overlay exactly once.  Restore
        its visibility even when saving fails.  No simulation, layer or QGZ
        contents are changed.  Image dimensions equal the canvas screenshot.
        """
        if not self.isVisible() or not self._cuts:
            raise ValueError('有効な温度カラーバーがありません。地図に温度を表示してください。')
        if not destination or not str(destination).lower().endswith('.png'):
            raise ValueError('保存先はPNGファイル（.png）を指定してください。')
        if self._canvas.width() < 10 or self._canvas.height() < 10:
            raise ValueError('地図キャンバスが小さすぎるため画像を保存できません。')
        legend_image = self.grab()
        legend_xy = self.pos()
        self.hide()
        try:
            map_image = self._canvas.grab()
        finally:
            self.show()
            self.raise_()
        if map_image.isNull() or legend_image.isNull():
            raise RuntimeError('地図または温度凡例を画像として取得できませんでした。')
        painter = QPainter(map_image)
        try:
            painter.drawPixmap(legend_xy, legend_image)
        finally:
            painter.end()
        if not map_image.save(destination, 'PNG'):
            raise OSError('PNGを書き込めませんでした。保存先の権限・空き容量を確認してください。')
        return map_image.width(), map_image.height()

    def shutdown(self):
        try:
            self._canvas.removeEventFilter(self)
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()

    def paintEvent(self, event):
        if not self._cuts:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            p.scale(self._effective_scale, self._effective_scale)
            p.setPen(QPen(QColor('#999999'), 0.7))
            p.setBrush(QColor(250, 250, 250, 242))
            p.drawRoundedRect(QRectF(.5, .5, BAR_WIDTH-1, self._base_height()-1), 7, 7)

            p.setPen(QColor('#222222'))
            f = QFont(self.font())
            f.setPointSize(10)
            f.setBold(True)
            p.setFont(f)
            label = ('ΔMRT (B−A)' if self._kind == 'delta_mrt' else
                     'Surface Temperature' if self._kind == 'surface' else 'MRT')
            p.drawText(QRectF(12, 7, BAR_WIDTH-24, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       ((self._prefix + ' | ') if self._prefix else '') + label + '  [°C]')

            f.setPointSize(9)
            f.setBold(False)
            p.setFont(f)
            p.drawText(QRectF(12, 29, BAR_WIDTH-24, 15), Qt.AlignLeft | Qt.AlignVCenter,
                       self._timestamp)

            x, y, width, height = 16., 50., float(BAR_WIDTH-32), 19.
            w = width / len(self._cuts)
            for idx, (lo, hi, color) in enumerate(self._cuts):
                p.fillRect(QRectF(x+idx*w, y, w+.25, height), QColor(color))
            p.setPen(QColor('#555555'))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(x, y, width, height))

            f.setPointSize(8)
            p.setFont(f)
            p.setPen(QColor('#252525'))
            ticks = scale_ticks(self._cuts)
            for i, value in enumerate(ticks):
                tx = x + width*i/len(self._cuts)
                p.drawLine(QPointF(tx, y+height), QPointF(tx, y+height+4))
                p.drawText(QRectF(tx-21, y+height+6, 42, 14), Qt.AlignCenter,
                           '%.1f' % value)

            if self._show_missing_note:
                sw_x, sw_y, sw_s = 17., 99., 7.
                for yy in range(2):
                    for xx in range(2):
                        p.fillRect(QRectF(sw_x+xx*sw_s, sw_y+yy*sw_s, sw_s, sw_s),
                                   QColor('#dddddd' if (xx+yy)%2==0 else '#ffffff'))
                p.setPen(QColor('#777777'))
                p.drawRect(QRectF(sw_x, sw_y, sw_s*2, sw_s*2))
                p.setPen(QColor('#555555'))
                p.drawText(QRectF(39, 97, BAR_WIDTH-51, 18), Qt.AlignLeft | Qt.AlignVCenter,
                           ('差分なし：対応点なし・欠測（補間なし）' if self._kind == 'delta_mrt' else
                            '温度未描画：下地が表示（原因は別途診断）'))
        finally:
            p.end()

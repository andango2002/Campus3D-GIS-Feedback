# -*- coding: utf-8 -*-
"""Phase 2: QGIS-only visualization controls.

Never changes the source GPKG, scientific values, geometry, or any of the six
v0.5.0 computational engines.  Surface and MRT temperature scales are kept
separate and each dataset is keyed by manifest export directory and timestamp.
"""
import math
import os

SURFACE_COLORS = ('#2369d4', '#30bfe2', '#55c58b', '#eee057', '#ee9135', '#d84c38')
MRT_COLORS = ('#4c1070', '#16bad8', '#95ee48', '#f58b36', '#a82429')


def parse_campus3d_temperature_layer(layer):
    """Return (quantity, directory, layer file name) or None.

    This intentionally excludes unrelated QGIS data with similarly named fields.
    """
    try:
        if not layer.isValid() or layer.providerType() != 'ogr':
            return None
        src = layer.source().split('|', 1)[0]
        filename = os.path.basename(src.replace('\\', '/')).lower()
        if filename in ('surface_ground.gpkg', 'surface_roof.gpkg', 'surface_wall.gpkg'):
            field = 'surface_c'
            kind = 'surface'
        elif filename == 'mrt_points.gpkg':
            field = 'mrt_c'
            kind = 'mrt'
        else:
            return None
        names = set(layer.fields().names())
        if field not in names or 'time_jst' not in names or layer.featureCount() <= 0:
            return None
        return kind, os.path.normcase(os.path.realpath(os.path.dirname(src))), filename
    except Exception:
        return None


def dataset_key(kind, folder, timestamp):
    """Group GROUND/ROOF/WALL only when both origin and time match."""
    return (kind, folder, str(timestamp))


def valid_range(low, high):
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError('温度の上下限は有限の数値にしてください。')
    if low >= high:
        raise ValueError('表示範囲の上限は下限より大きくしてください。')
    return float(low), float(high)


def data_range(layers, field):
    """Read provider min/max without iterating all features."""
    values = []
    for layer in layers:
        idx = layer.fields().indexFromName(field)
        if idx < 0:
            raise ValueError('温度フィールドがありません: ' + field)
        minval = layer.dataProvider().minimumValue(idx)
        maxval = layer.dataProvider().maximumValue(idx)
        try:
            a, b = float(minval), float(maxval)
        except (TypeError, ValueError):
            continue
        if math.isfinite(a) and math.isfinite(b):
            values.append((a, b))
    if not values:
        raise ValueError('このレイヤに有効な温度値がありません。')
    low, high = min(a for a, _ in values), max(b for _, b in values)
    if high - low < 0.001:
        low -= 1.0
        high += 1.0
    return low, high


def breaks(low, high, colors):
    valid_range(low, high)
    return [(low + (high - low) * i / len(colors),
             low + (high - low) * (i + 1) / len(colors), color)
            for i, color in enumerate(colors)]


def apply_style(layers, kind, low, high):
    """Only changes renderer, not features, files or geometry."""
    from qgis.core import (QgsGraduatedSymbolRenderer, QgsRendererRange,
                           QgsFillSymbol, QgsMarkerSymbol, QgsWkbTypes)
    if kind not in ('surface', 'mrt'):
        raise ValueError('未対応の表示種類です。')
    if not layers:
        raise ValueError('表示対象レイヤがありません。')
    colors = SURFACE_COLORS if kind == 'surface' else MRT_COLORS
    field = 'surface_c' if kind == 'surface' else 'mrt_c'
    cuts = breaks(low, high, colors)
    for layer in layers:
        if layer.fields().indexFromName(field) < 0:
            raise ValueError('対象レイヤに温度フィールドがありません: ' + layer.name())
    for layer in layers:
        segments = []
        point = layer.geometryType() == QgsWkbTypes.PointGeometry
        for a, b, color in cuts:
            if point:
                sym = QgsMarkerSymbol.createSimple({
                    'name': 'circle', 'color': color, 'outline_style': 'no', 'size': '1.7'})
            else:
                sym = QgsFillSymbol.createSimple({
                    'color': color, 'outline_style': 'no'})
            segments.append(QgsRendererRange(a, b, sym, f'{a:.1f} – {b:.1f} °C'))
        layer.setRenderer(QgsGraduatedSymbolRenderer(field, segments))
        layer.triggerRepaint()
    return cuts


def legend_html(cuts, quantity):
    """Dock label preview. QGIS legend remains derived from graduated renderer."""
    if not cuts:
        return '表示対象を選択してください。'
    table = ''.join('<td style="background:%s; width:34px; height:13px"></td>' % c
                    for _, _, c in cuts)
    return ('<b>%s</b>　%.1f ～ %.1f °C<br>' %
            ('Surface Temperature' if quantity == 'surface' else 'MRT',
             cuts[0][0], cuts[-1][1]) +
            '<table cellspacing="0" cellpadding="0"><tr>' + table +
            '</tr></table><small>レイヤツリーの凡例にも温度階級を表示します。</small>')

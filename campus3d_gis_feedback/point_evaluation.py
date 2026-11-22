# -*- coding: utf-8 -*-
"""Point-query adapter for already loaded Campus3D GIS Feedback GPKGs.

GROUND and ROOF: exact plan-view intersection with triangular patches.
WALL: nearest centroid within a deliberately smaller fixed radius.
MRT: nearest precomputed MRT evaluation point within a user-chosen radius,
     plus descriptive stats of evaluation points within that radius.

Does not modify source layers, interpolate missing temperatures or write data.
"""
import math

from qgis.core import (
    QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry, QgsPointXY,
    QgsProject, QgsRectangle, Qgis,
)
from .point_evaluation_core import (
    patch_temperature_summary, nearest_summary, finite_number,
)

WALL_RADIUS_M = 2.0
POLYGON_QUERY_EPS_M = 0.001


def _projected_point(canvas_point, canvas_crs, layer):
    if not layer.isValid():
        raise ValueError('レイヤが無効です: ' + layer.name())
    crs = layer.crs()
    if not crs.isValid() or crs.isGeographic() or crs.mapUnits() != Qgis.DistanceUnit.Meters:
        raise ValueError('評価にはメートル単位の投影座標が必要です: ' + layer.name())
    if canvas_crs == crs:
        return QgsPointXY(canvas_point)
    return QgsCoordinateTransform(canvas_crs, crs, QgsProject.instance()).transform(canvas_point)


def _matching_time(feature, time_stamp):
    try:
        return str(feature['time_jst']) == str(time_stamp)
    except (KeyError, TypeError, IndexError):
        return False


def _polygon_values(layer, clicked, time_stamp):
    """Bounding-box prefilter followed by *exact* point/triangle intersection."""
    x, y = clicked.x(), clicked.y()
    eps = POLYGON_QUERY_EPS_M
    rect = QgsRectangle(x-eps, y-eps, x+eps, y+eps)
    request = QgsFeatureRequest().setFilterRect(rect)
    point_geom = QgsGeometry.fromPointXY(clicked)
    found = []
    for f in layer.getFeatures(request):
        if not _matching_time(f, time_stamp):
            continue
        g = f.geometry()
        if not g or g.isEmpty() or not g.intersects(point_geom):
            continue
        temp = finite_number(f['surface_c'])
        if temp is None:
            continue
        found.append((f['patch_id'], temp))
    return found


def _point_values(layer, clicked, time_stamp, field, id_field, radius):
    """Within a metric search radius, return only original sample point values."""
    x, y = clicked.x(), clicked.y()
    request = QgsFeatureRequest().setFilterRect(
        QgsRectangle(x-radius, y-radius, x+radius, y+radius))
    query_point = QgsGeometry.fromPointXY(clicked)
    found = []
    for f in layer.getFeatures(request):
        if not _matching_time(f, time_stamp):
            continue
        geom = f.geometry()
        if not geom or geom.isEmpty():
            continue
        dist = float(geom.distance(query_point))
        if not math.isfinite(dist) or dist > radius + 1e-8:
            continue
        temp = finite_number(f[field])
        if temp is None:
            continue
        found.append((f[id_field], temp, dist))
    return found


def evaluate_point(canvas_point, canvas_crs, dataset, mrt_radius_m):
    """Return ready-to-copy Japanese report for one click.

    dataset is [ (kind, folder, timestamp, [layers]), ... ] from the selected
    same-run dataset group.  Layers may be invisible; the explicit selection
    (not map layer visibility) defines the calculation target.
    """
    surface = next((item for item in dataset if item[0] == 'surface'), None)
    mrt = next((item for item in dataset if item[0] == 'mrt'), None)
    if surface is None and mrt is None:
        raise ValueError('評価対象の解析レイヤがありません。')
    baselayer = (surface[3][0] if surface else mrt[3][0])
    basepoint = _projected_point(canvas_point, canvas_crs, baselayer)
    ts = surface[2] if surface is not None else mrt[2]
    lines = [f'点評価｜{ts}',
             f'位置：{baselayer.crs().authid()}  X={basepoint.x():.3f} m  Y={basepoint.y():.3f} m',
             '', '【Surface Temperature：計算済みパッチ】']
    if surface is None:
        lines.append('Surfaceレイヤがありません。')
    else:
        layers_byrole = {}
        for lyr in surface[3]:
            filename = lyr.source().split('|', 1)[0].replace('\\', '/').lower()
            role = ('GROUND' if filename.endswith('surface_ground.gpkg') else
                    'ROOF' if filename.endswith('surface_roof.gpkg') else
                    'WALL' if filename.endswith('surface_wall.gpkg') else None)
            if role is not None:
                layers_byrole[role] = lyr
        for role in ('GROUND', 'ROOF'):
            layer = layers_byrole.get(role)
            if layer is None:
                lines.append(f'{role}: レイヤなし')
                continue
            point = _projected_point(canvas_point, canvas_crs, layer)
            values = _polygon_values(layer, point, ts)
            lines.append(f'{role}: {patch_temperature_summary(values)}')
        wall_layer = layers_byrole.get('WALL')
        if wall_layer is None:
            lines.append('WALL: レイヤなし')
        else:
            point = _projected_point(canvas_point, canvas_crs, wall_layer)
            values = _point_values(wall_layer, point, ts,
                                   'surface_c', 'patch_id', WALL_RADIUS_M)
            lines.append(f'WALL重心近傍: {nearest_summary(values, WALL_RADIUS_M, "WALL")}')
            lines.append('  ※WALLは壁の2D投影ポリゴンではなく重心点で評価')
    lines.extend(['', '【MRT：計算済み評価点】'])
    if mrt is None:
        lines.append('同一時刻・同一解析親フォルダのMRTレイヤがありません。')
    else:
        if len(mrt[3]) != 1:
            lines.append('MRT評価点レイヤが複数あり一意に選べません。')
        else:
            layer = mrt[3][0]
            point = _projected_point(canvas_point, canvas_crs, layer)
            values = _point_values(layer, point, ts, 'mrt_c', 'point_id', mrt_radius_m)
            lines.append(nearest_summary(values, mrt_radius_m, 'MRT'))
    lines.extend(['', '※既存GIS出力の値を読み取っています。補間・再計算はしていません。',
                  '※GROUNDとROOFが重なる場合も別々に表示します。'])
    return '\n'.join(lines)

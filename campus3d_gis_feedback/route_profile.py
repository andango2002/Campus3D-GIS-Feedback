# -*- coding: utf-8 -*-
"""QGIS adapter: profile of calculated MRT points near a user-drawn route."""
import math
from qgis.core import QgsPointXY
from .point_evaluation import _projected_point, _point_values
from .route_profile_core import route_samples, choose_nearest, route_statistics


def evaluate_route(canvas_vertices, canvas_crs, dataset, sample_step_m, radius_m):
    mrt = next((r for r in dataset if r[0] == 'mrt'), None)
    if mrt is None or len(mrt[3]) != 1:
        raise ValueError('選択データに一意のMRT評価点レイヤがありません。')
    layer = mrt[3][0]
    for field in ('mrt_c','point_id','time_jst'):
        if layer.fields().indexFromName(field) < 0:
            raise ValueError('MRTレイヤに属性がありません: ' + field)
    projected = [_projected_point(QgsPointXY(x,y), canvas_crs, layer)
                 for x,y in canvas_vertices]
    samples = route_samples([(p.x(),p.y()) for p in projected], sample_step_m)
    stamp = mrt[2]
    rows = []
    for distance, x, y in samples:
        point = QgsPointXY(x,y)
        found = _point_values(layer, point, stamp, 'mrt_c', 'point_id', float(radius_m))
        closest = choose_nearest(found, radius_m)
        rows.append({
            'distance_m': distance, 'x_m':x,'y_m':y,
            'mrt_c':closest[1] if closest else None,
            'point_id':closest[0] if closest else None,
            'nearest_distance_m':closest[2] if closest else None,
        })
    stats = route_statistics(rows)
    lines = [
        f'線評価｜MRT｜{stamp}',
        f'座標系：{layer.crs().authid()}（投影メートル座標）',
        f'経路長：{stats["length_m"]:.1f} m / 頂点：{len(projected)} / 探索半径：{radius_m:g} m / サンプル間隔：{sample_step_m:g} m',
        f'有効サンプル：{stats["valid_samples"]}/{stats["samples"]}（欠測{stats["missing_samples"]}）',
        f'有効区間の目安：{stats["represented_length_m"]:.1f} m / {stats["coverage_percent"]:.1f} %',
        f'参照した異なるMRT評価点：{stats["unique_point_ids"]}点',
    ]
    if stats['weighted_mean_c'] is None:
        lines.append('平均・最小・最大：算出不可（探索半径内にMRT評価点なし）')
    else:
        lines.append('有効部分の距離重み付き代表値：%.2f °C / 最小 %.2f °C / 最大 %.2f °C' % (
            stats['weighted_mean_c'],stats['minimum_c'],stats['maximum_c']))
    lines.extend([
        '', '※MRTは経路上の温度を補間・再計算した値ではありません。',
        '※各サンプルは探索半径内の最近傍「計算済みMRT評価点」を使います。',
        '※同じ評価点が複数サンプルで繰り返し使われる場合があります。',
        '※距離重み付き代表値と有効率はサンプリングに基づく近似であり、',
        '  連続的な経路温度、歩行時の暴露量、実際の被覆面積を表すものではありません。',
        '※データのない区間は欠測として表示し、0℃や隣接値で埋めません。',
    ])
    return rows, stats, '\n'.join(lines), stamp, layer.crs().authid()

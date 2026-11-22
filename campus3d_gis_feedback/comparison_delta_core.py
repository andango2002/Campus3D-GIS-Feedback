# -*- coding: utf-8 -*-
"""Pure, audit-friendly MRT A/B pairing. No interpolation or nearest substitution.

Every record is (point_id, x_gis_m, y_gis_m, z_physical_m, mrt_c).
A and B may be different timestamps or designs, but must share the same
projected-metre CRS and the same physical evaluation locations.
"""
import math


def _index(records, label):
    result = {}
    for row in records:
        if len(row) != 5:
            raise ValueError('%sの評価点データに必要な5項目がありません。' % label)
        pid = int(row[0])
        if pid in result:
            raise ValueError('%sに重複したpoint_idがあります: %s' % (label, pid))
        vals = tuple(float(v) for v in row[1:])
        if not all(math.isfinite(v) for v in vals):
            raise ValueError('%sの評価点に無効な座標・温度があります: %s' % (label, pid))
        result[pid] = vals
    return result


def pair_mrt_records(a_records, b_records, xy_tolerance_m=0.05, z_tolerance_m=0.05):
    """Return matched records and an explicit audit of rejected points.

    Record: (point_id, x, y, z_A, z_B, temp_A, temp_B, B_minus_A).
    Identical ID alone is NOT sufficient. XY and physical-height checks apply.
    """
    a = _index(a_records, 'A'); b = _index(b_records, 'B')
    if not a or not b:
        raise ValueError('A/BにMRT評価点がありません。')
    if not (math.isfinite(xy_tolerance_m) and 0 < xy_tolerance_m <= 1):
        raise ValueError('XY照合許容差が無効です。')
    if not (math.isfinite(z_tolerance_m) and 0 < z_tolerance_m <= 1):
        raise ValueError('高さ照合許容差が無効です。')
    common = set(a) & set(b)
    paired, different_xy, different_z = [], 0, 0
    for pid in sorted(common):
        ax, ay, az, at = a[pid]
        bx, by, bz, bt = b[pid]
        if math.hypot(bx-ax, by-ay) > xy_tolerance_m:
            different_xy += 1
            continue
        if abs(bz-az) > z_tolerance_m:
            different_z += 1
            continue
        paired.append((pid, ax, ay, az, bz, at, bt, bt-at))
    if not paired:
        raise ValueError('共通位置・高さのMRT評価点がありません。異なる格子の空間照合は未実装です。')
    diff = [p[-1] for p in paired]
    return paired, {
        'count_A': len(a), 'count_B': len(b), 'common_point_ids': len(common),
        'only_A': len(a)-len(common), 'only_B': len(b)-len(common),
        'same_id_xy_mismatch': different_xy,
        'same_id_height_mismatch': different_z,
        'matched': len(paired), 'matched_pct_of_A': 100*len(paired)/len(a),
        'matched_pct_of_B': 100*len(paired)/len(b),
        'xy_tolerance_m': xy_tolerance_m, 'height_tolerance_m': z_tolerance_m,
        'delta_min_c': min(diff), 'delta_max_c': max(diff),
        'delta_mean_c': sum(diff)/len(diff),
        'positive_points': sum(d>1e-10 for d in diff),
        'negative_points': sum(d < -1e-10 for d in diff),
        'zero_points': sum(abs(d)<=1e-10 for d in diff),
        'note': 'MRT evaluation points are paired by point_id, XY and physical height; no spatial or temporal interpolation.'
    }


def diverging_breaks(delta_values, colors=None):
    colors = tuple(colors or ('#165b9a','#3487bf','#a8d1e8','#f1f1ef','#f2b8a6','#d76648','#a52228'))
    if len(colors)!=7:
        raise ValueError('7色の発散配色が必要です。')
    vals=list(delta_values)
    if not vals or not all(math.isfinite(float(v)) for v in vals):
        raise ValueError('温度差分が無効です。')
    max_abs=max(abs(float(v)) for v in vals)
    # Symmetric even when delta is strictly positive or negative.
    edge=max(0.1, math.ceil(max_abs*10-1e-9)/10)
    return [(-edge+2*edge*i/7, -edge+2*edge*(i+1)/7, col)
            for i,col in enumerate(colors)]

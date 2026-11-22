# -*- coding: utf-8 -*-
"""Phase 3 point evaluation: pure grouping/formatting helpers.

No geometry is fabricated or interpolated.  The QGIS adapter reads native
GeoPackage feature values directly; these helpers keep pairing logic testable
outside QGIS.
"""
import math
import os


def build_point_datasets(visualization_sets):
    """Pair unique MRT/Surface exports only with same parent folder + timestamp.

    visualization_sets: iterable of (kind, folder, timestamp, [QGIS layers]).
    If duplicate exports exist for a quantity at the same parent/time, do not
    silently pair ambiguous runs; keep them as single-quantity choices.
    """
    bins = {}
    for kind, folder, stamp, layers in visualization_sets:
        if kind not in ('surface', 'mrt') or not layers:
            continue
        parent = os.path.normcase(os.path.realpath(os.path.dirname(folder)))
        key = (parent, str(stamp))
        bins.setdefault(key, {}).setdefault(kind, []).append((kind, folder, str(stamp), layers))
    result = []
    for key, bykind in sorted(bins.items(), key=lambda item: (item[0][1], item[0][0])):
        surface = bykind.get('surface', [])
        mrt = bykind.get('mrt', [])
        if len(surface) == 1 and len(mrt) == 1:
            result.append((('pair', key, 0), key[1], [surface[0], mrt[0]], False))
            continue
        # Ambiguous same-time duplicates must never be matched without proof.
        ambiguous = len(surface) > 1 or len(mrt) > 1
        for kind in ('surface', 'mrt'):
            for ix, entry in enumerate(bykind.get(kind, [])):
                result.append(((kind, key, ix), key[1], [entry], ambiguous))
    return result


def finite_number(value):
    """Read a numeric temperature or return None (QGIS NULL is not numeric)."""
    try:
        v = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def patch_temperature_summary(records):
    """Format exact point-in-polygon patches; no interpolation/averaging."""
    vals = sorted((int(pid), float(temp)) for pid, temp in records
                  if finite_number(temp) is not None)
    if not vals:
        return '該当パッチなし'
    if len(vals) == 1:
        pid, temp = vals[0]
        return f'{temp:.2f} °C（patch_id={pid}）'
    lo, hi = min(t for _, t in vals), max(t for _, t in vals)
    return (f'{lo:.2f}～{hi:.2f} °C（境界・重複 {len(vals)}パッチ。'
            '単一値に平均しません）')


def nearest_summary(records, radius, metric='MRT'):
    """records=(identifier, temp_c, distance_m); no spatial interpolation."""
    valid = [(str(ident), float(temp), float(dist)) for ident, temp, dist in records
             if finite_number(temp) is not None and finite_number(dist) is not None
             and 0 <= float(dist) <= float(radius) + 1e-8]
    if not valid:
        return f'半径{radius:g} m以内に評価点なし（補間しません）'
    valid.sort(key=lambda rec: (rec[2], rec[0]))
    ident, temp, d = valid[0]
    unit = 'point_id' if metric == 'MRT' else 'patch_id'
    value = f'{temp:.2f} °C（最近傍 {d:.2f} m、{unit}={ident}）'
    if metric == 'MRT':
        temps = [t for _, t, _ in valid]
        value += (f'\n    周辺{len(valid)}点：平均 {sum(temps)/len(temps):.2f} °C、'
                  f'最小 {min(temps):.2f}、最大 {max(temps):.2f} °C'
                  '\n    ※周辺平均は計算済み評価点の単純平均（補間値ではありません）')
    return value

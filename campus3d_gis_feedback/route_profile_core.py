# -*- coding: utf-8 -*-
"""Pure numeric helpers for Phase 3 MRT route profiles.

A polyline is sampled in a projected metric CRS at fixed chainage.  Source
MRT values are never spatially interpolated; nearest measured/calculated
MRT *points* are selected only within the explicitly declared tolerance.
The graph's connecting strokes are visualization, not synthesized values.
"""
import math

MAX_ROUTE_M = 20000.0
MAX_SAMPLES = 2001
MAX_VERTICES = 200


def route_samples(vertices, spacing_m):
    """Return (chainage, x, y) including end point, retaining total length.

    Consecutive duplicate vertices are ignored; zero-length routes rejected.
    """
    if not 1 <= len(vertices) <= MAX_VERTICES:
        raise ValueError('経路の頂点数は1～%d点にしてください。' % MAX_VERTICES)
    spacing = float(spacing_m)
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError('サンプリング間隔を正の値にしてください。')
    clean = []
    for x, y in vertices:
        x, y = float(x), float(y)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('経路座標が有限値ではありません。')
        if not clean or math.hypot(x-clean[-1][0], y-clean[-1][1]) > 1e-7:
            clean.append((x, y))
    if len(clean) < 2:
        raise ValueError('異なる2地点以上を指定してください。')
    segments, total = [], 0.0
    for a,b in zip(clean, clean[1:]):
        length = math.hypot(b[0]-a[0], b[1]-a[1])
        segments.append((total, total+length, a, b))
        total += length
    if total > MAX_ROUTE_M:
        raise ValueError('試験版の経路上限は%.0fmです。' % MAX_ROUTE_M)
    chainages = [i * spacing for i in range(int(math.floor(total / spacing)) + 1)]
    if total - chainages[-1] > 1e-7:
        chainages.append(total)
    else:
        chainages[-1] = total
    if len(chainages) > MAX_SAMPLES:
        raise ValueError('試験版のサンプル上限は%d点です。間隔を広げてください。' % MAX_SAMPLES)
    out, segidx = [], 0
    for d in chainages:
        while segidx < len(segments)-1 and d > segments[segidx][1]+1e-9:
            segidx += 1
        start,end,a,b = segments[segidx]
        f = max(0.0, min(1.0, (d-start)/(end-start)))
        out.append((d, a[0]+f*(b[0]-a[0]), a[1]+f*(b[1]-a[1])))
    return out


def choose_nearest(records, radius):
    """records are (point_id, temp_C, distance_m). Returns tuple or None."""
    valid = []
    radius = float(radius)
    for ident, t, dist in records:
        try:
            t, dist = float(t), float(dist)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(t) and math.isfinite(dist) and 0 <= dist <= radius + 1e-8:
            valid.append((str(ident), t, dist))
    return min(valid, key=lambda r: (r[2], r[0])) if valid else None


def route_statistics(rows):
    """Nearest-point route summary using midpoint-cell length weights.

    rows: dicts with distance_m, mrt_c (None for missing), point_id. Weight for
    every sample covers halfway to neighboring samples.  Thus percent refers
    to the route *represented by samples* (not an exact continuous coverage
    or time/physical exposure).  No interpolation across missing segments.
    """
    if len(rows) < 2:
        raise ValueError('少なくとも2つのサンプルが必要です。')
    distances = [float(r['distance_m']) for r in rows]
    if any(b <= a for a,b in zip(distances, distances[1:])):
        raise ValueError('サンプル距離は昇順の必要があります。')
    total = distances[-1] - distances[0]
    valid, weight_total, weight_valid, weighted_sum = [], 0.0, 0.0, 0.0
    for i, r in enumerate(rows):
        left = distances[0] if i == 0 else (distances[i-1]+distances[i]) / 2
        right = distances[-1] if i == len(rows)-1 else (distances[i]+distances[i+1]) / 2
        weight = right - left
        weight_total += weight
        t = r.get('mrt_c')
        if t is not None and isinstance(t, (int,float)) and math.isfinite(float(t)):
            t = float(t)
            valid.append(t)
            weight_valid += weight
            weighted_sum += weight*t
    unique_ids = {str(r['point_id']) for r in rows if r.get('mrt_c') is not None and r.get('point_id') is not None}
    return {
        'length_m': total,
        'samples': len(rows),
        'valid_samples': len(valid),
        'missing_samples': len(rows)-len(valid),
        'coverage_percent': 100.0*weight_valid/weight_total if weight_total > 0 else 0.0,
        'represented_length_m': weight_valid,
        'weighted_mean_c': weighted_sum/weight_valid if weight_valid > 0 else None,
        'minimum_c': min(valid) if valid else None,
        'maximum_c': max(valid) if valid else None,
        'unique_point_ids': len(unique_ids),
    }

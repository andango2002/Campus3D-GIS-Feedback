# -*- coding: utf-8 -*-
"""Pure Phase 4.2 MRT matched-location spatial comparison.

A and B are first paired by point ID, XY and physical Z (v0.7.1 policy).
All point/route/area results are drawn from that *same* matched population.
No nearest matching between different A/B grids; no interpolation.
"""
import math
from collections import defaultdict
from .comparison_delta_core import pair_mrt_records
from .route_profile_core import route_samples, route_statistics


class MatchedMRT:
    """Spatial hash over *already paired* points; query returns one shared ID."""
    def __init__(self, a_rows, b_rows, bin_size=20.0):
        self.points, self.audit = pair_mrt_records(a_rows, b_rows)
        self.bin_size=float(bin_size)
        self.bins=defaultdict(list)
        for p in self.points:
            self.bins[(math.floor(p[1]/self.bin_size), math.floor(p[2]/self.bin_size))].append(p)

    def nearest(self, x, y, radius_m):
        x,y,r=float(x),float(y),float(radius_m)
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(r) and r>0):
            raise ValueError('探索座標または半径が無効です。')
        side=self.bin_size
        lo_x=math.floor((x-r)/side); hi_x=math.floor((x+r)/side)
        lo_y=math.floor((y-r)/side); hi_y=math.floor((y+r)/side)
        best=None
        for i in range(lo_x,hi_x+1):
            for j in range(lo_y,hi_y+1):
                for p in self.bins.get((i,j),()):
                    d=math.hypot(p[1]-x,p[2]-y)
                    if d<=r+1e-8 and (best is None or (d,p[0])<(best[1],best[0][0])):
                        best=(p,d)
        return best


def paired_route(index, vertices, step_m, radius_m):
    samples=route_samples(vertices,step_m)
    rows=[]
    for distance,x,y in samples:
        match=index.nearest(x,y,radius_m)
        p=match[0] if match else None
        rows.append(dict(distance_m=distance,x_m=x,y_m=y,point_id=p[0] if p else None,
                         nearest_distance_m=match[1] if match else None,
                         a_c=p[5] if p else None,b_c=p[6] if p else None,
                         delta_c=p[7] if p else None,
                         status='MATCHED' if p else 'NO_COMMON_POINT'))
    def summarize(field):
        return route_statistics([dict(distance_m=row['distance_m'],mrt_c=row[field],point_id=row['point_id'])
                                 for row in rows])
    return rows, {'A':summarize('a_c'),'B':summarize('b_c'),'delta':summarize('delta_c')}


def paired_area(index, contains_fn, threshold_c):
    """contains_fn(x,y) is an exact plan-view polygon containment predicate."""
    t=float(threshold_c)
    if not math.isfinite(t):raise ValueError('高温判定値が無効です。')
    data=[p for p in index.points if contains_fn(p[1],p[2])]
    if not data:
        return {'matched':0,'A':None,'B':None,'delta':None,'A_hot_pct':None,'B_hot_pct':None,
                'cooling_pct':None,'points':[]}
    def stats(vals):
        avg=sum(vals)/len(vals)
        return dict(mean=avg,minimum=min(vals),maximum=max(vals),
                    sd=math.sqrt(sum((v-avg)**2 for v in vals)/len(vals)))
    return dict(matched=len(data),A=stats([p[5] for p in data]),
                B=stats([p[6] for p in data]),delta=stats([p[7] for p in data]),
                A_hot_pct=100*sum(p[5]>=t for p in data)/len(data),
                B_hot_pct=100*sum(p[6]>=t for p in data)/len(data),
                cooling_pct=100*sum(p[7]<0 for p in data)/len(data),points=data)

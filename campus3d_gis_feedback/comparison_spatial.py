# -*- coding: utf-8 -*-
"""QGIS adapter for v0.7.2 A/B point, route and area evaluations.

MRT comparison: strictly same point_id, physical XY/Z, with one nearest shared
point per route sample. Surface: independent polygon containment/area summaries,
never claims patch correspondence between designs.
"""
import math
from qgis.core import (QgsGeometry,QgsPointXY,QgsCoordinateTransform,QgsProject,
                       QgsWkbTypes,Qgis)
from .comparison_delta import _extract
from .comparison_spatial_core import MatchedMRT, paired_area, paired_route
from .point_evaluation import _polygon_values
from .area_evaluation import evaluate_area
from .comparison_core import validate_pair,role


def _transform_vertices(vertices, canvas_crs, target_crs):
    if not target_crs.isValid() or target_crs.isGeographic() or target_crs.mapUnits()!=Qgis.DistanceUnit.Meters:
        raise ValueError('解析には投影メートル座標系を使用してください。')
    if not canvas_crs.isValid():raise ValueError('QGISの地図座標系が無効です。')
    transform=(QgsCoordinateTransform(canvas_crs,target_crs,QgsProject.instance())
               if canvas_crs!=target_crs else None)
    points=[]
    for x,y in vertices:
        p=QgsPointXY(float(x),float(y))
        p=transform.transform(p) if transform is not None else p
        if not (math.isfinite(p.x()) and math.isfinite(p.y())):
            raise ValueError('投影変換後の座標が無効です。')
        points.append((p.x(),p.y()))
    return points


def _polygon(coords):
    if len(coords)<3 or len(set(coords))<3 or len(coords)>200:
        raise ValueError('異なる3～200点で区域を指定してください。')
    ring=[QgsPointXY(x,y) for x,y in coords]
    if ring[0]!=ring[-1]:ring.append(ring[0])
    geom=QgsGeometry.fromPolygonXY([ring])
    if geom.isEmpty() or geom.isNull() or not geom.isGeosValid():
        raise ValueError('無効な区域ポリゴンです。自己交差を避けて描き直してください。')
    if geom.area()<0.01:raise ValueError('区域が小さすぎます。')
    return geom


def _surface_roles(entry):
    return {role(layer).split('.')[0].replace('surface_','').upper():layer for layer in entry[3]
            if role(layer) in ('surface_ground.gpkg','surface_roof.gpkg')}


def calculate(a,b,mode,vertices,canvas_crs,radius_m=20.,step_m=10.,threshold_c=40.):
    """Return report, CSV rows, graph rows, audit. Does not write any source data."""
    kind=validate_pair(a,b)
    if mode not in ('point','route','area'):raise ValueError('未対応の比較モードです。')
    if mode=='route' and kind!='mrt':
        raise ValueError('この版の経路比較はMRT専用です。Surfaceは点・面比較を選択してください。')
    al=a[3][0];bl=b[3][0]
    crs=al.crs()
    if crs!=bl.crs() or any(l.crs()!=crs for e in (a,b) for l in e[3]):
        raise ValueError('A/Bの座標系が異なります。同一の投影座標系で比較してください。')
    coords=_transform_vertices(vertices,canvas_crs,crs)
    if (mode=='point' and len(coords)!=1 or mode=='route' and len(coords)<2 or
        mode=='area' and len(coords)<3):
        raise ValueError('地点・経路・区域の指定が不足しています。')
    lines=[f'A/B {"点" if mode=="point" else "線" if mode=="route" else "面"}評価｜{kind.upper()}',
           f'A：{a[2]}',f'B：{b[2]}',f'座標系：{crs.authid()}','']
    if a[2]!=b[2]:
        lines.append('【注意】解析日時が異なるため、温度差は時間変化であり、設計変更単独の効果ではありません。')
    else:
        lines.append('【注意】同時刻でも気象条件・モデル等が一致するかは別途確認してください。')
    csv_rows=[];graph=[]; audit={'kind':kind,'mode':mode,'crs':crs.authid(),
                                'A':a[1],'B':b[1],'timestamp_A':a[2],'timestamp_B':b[2]}
    audit.update(radius_m=radius_m if kind=='mrt' and mode in ('point','route') else None,
                 step_m=step_m if mode=='route' else None,
                 threshold_c=threshold_c if mode=='area' else None)
    if mode=='point':audit['geometry_wkt']='POINT (%.12f %.12f)'%coords[0]
    elif mode=='route':audit['geometry_wkt']='LINESTRING ('+', '.join('%.12f %.12f'%p for p in coords)+')'
    else:audit['geometry_wkt']=_polygon(coords).asWkt(12)
    if kind=='mrt':
        if len(a[3])!=1 or len(b[3])!=1:
            raise ValueError('A/BともMRTレイヤが1つずつ必要です。')
        matches=MatchedMRT(_extract(al),_extract(bl))
        audit['matching']=matches.audit
        lines.append('厳密照合：%d点（A %.1f%%／B %.1f%%）、XY・高さ一致点のみ。'%(
            matches.audit['matched'],matches.audit['matched_pct_of_A'],matches.audit['matched_pct_of_B']))
        if min(matches.audit['matched_pct_of_A'],matches.audit['matched_pct_of_B'])<95:
            lines.append('【注意】対応率が95%未満。比較の対象集団が限定されています。')
        lines.append('')
        if mode=='point':
            x,y=coords[0]; match=matches.nearest(x,y,radius_m)
            lines.append(f'位置 X={x:.3f} m / Y={y:.3f} m｜探索半径 {radius_m:g} m')
            if not match:
                lines.append('共通MRT評価点なし。AとBの単独値を組み合わせて差は作りません。')
                csv_rows.append(dict(x_m=x,y_m=y,status='NO_COMMON_POINT'))
            else:
                p,d=match
                lines.append(f'共通point_id={p[0]}｜距離={d:.2f}m｜物理高さ A={p[3]:.2f}m / B={p[4]:.2f}m')
                lines.append(f'MRT：A={p[5]:.2f}℃｜B={p[6]:.2f}℃｜Δ(B−A)={p[7]:+.2f}℃')
                csv_rows.append(dict(x_m=x,y_m=y,point_id=p[0],nearest_distance_m=d,
                                     a_c=p[5],b_c=p[6],delta_c=p[7],status='MATCHED'))
        elif mode=='route':
            rows,stats=paired_route(matches,coords,step_m,radius_m)
            graph=rows;csv_rows=rows
            s=stats['delta']
            lines.extend([f'経路長：{s["length_m"]:.1f}m｜間隔 {step_m:g}m／半径 {radius_m:g}m',
                          f'共通評価点を参照したサンプル：{s["valid_samples"]}/{s["samples"]}（代表距離割合{s["coverage_percent"]:.1f}%）',
                          f'参照した異なるpoint_id：{s["unique_point_ids"]}点'])
            if s['weighted_mean_c'] is not None:
                lines.append('有効区間の距離重み付き代表：A %.2f℃／B %.2f℃／Δ %+.2f℃'%(
                    stats['A']['weighted_mean_c'],stats['B']['weighted_mean_c'],s['weighted_mean_c']))
                lines.append('Δ範囲：%+.2f～%+.2f℃'%(s['minimum_c'],s['maximum_c']))
            else:lines.append('有効な共通MRT評価点がなく、差分は計算できません。')
            lines.append('※各サンプルは共通の最近傍point_idを使用。非対応地点の差分や補間はしません。')
            lines.append('※距離代表値はサンプリング近似です。歩行時の暴露量を表しません。')
            audit['route_stats']=stats
        else:
            region=_polygon(coords)
            def inside(x,y):
                return region.intersects(QgsGeometry.fromPointXY(QgsPointXY(x,y)))
            s=paired_area(matches,inside,threshold_c)
            audit.update(region_wkt=region.asWkt(12),region_area_m2=region.area(),
                         threshold_c=threshold_c,matched_inside=s['matched'])
            lines.extend([f'区域面積：{region.area():,.2f}m²｜高温閾値：{threshold_c:.1f}℃',
                          f'区域内の対応MRT点：{s["matched"]}点'])
            if s['matched']:
                lines.append('同じ点の平均：A %.2f℃／B %.2f℃／Δ %+.2f℃'%(
                    s['A']['mean'],s['B']['mean'],s['delta']['mean']))
                lines.append('Δの最小%+.2f℃／最大%+.2f℃／標準偏差%.2f℃'%(
                    s['delta']['minimum'],s['delta']['maximum'],s['delta']['sd']))
                lines.append('%.1f℃以上の点数比：A %.1f%%／B %.1f%%｜Bが低温の点 %.1f%%'%(
                    threshold_c,s['A_hot_pct'],s['B_hot_pct'],s['cooling_pct']))
                csv_rows=[dict(point_id=p[0],x_m=p[1],y_m=p[2],z_a_m=p[3],z_b_m=p[4],
                               a_c=p[5],b_c=p[6],delta_c=p[7],status='MATCHED') for p in s['points']]
            else:lines.append('対応点が区域内にないため、温度差は計算できません。')
            lines.append('※MRTは区域内の対応評価点の単純統計。面積比ではありません。')
    else:  # Surface Temperature (A/B patch geometry is not assumed identical)
        roles_a=_surface_roles(a); roles_b=_surface_roles(b)
        if mode=='point':
            x,y=coords[0]
            lines.append(f'位置 X={x:.3f}m / Y={y:.3f}m｜各モデルのポリゴンと厳密に交差判定')
            for r in ('GROUND','ROOF'):
                if r not in roles_a or r not in roles_b:
                    lines.append(r+'：いずれかのレイヤなし'); continue
                pa=_polygon_values(roles_a[r],QgsPointXY(x,y),a[2]);pb=_polygon_values(roles_b[r],QgsPointXY(x,y),b[2])
                va=pa[0][1] if len(pa)==1 else None
                vb=pb[0][1] if len(pb)==1 else None
                delta=(vb-va) if va is not None and vb is not None else None
                lines.append('%s：A %s／B %s／Δ %s'%(r,
                    ('%.2f℃'%va if va is not None else '%dパッチ（単一値なし）'%len(pa)),
                    ('%.2f℃'%vb if vb is not None else '%dパッチ（単一値なし）'%len(pb)),
                    ('%+.2f℃'%delta if delta is not None else '不可')))
                csv_rows.append(dict(role=r,x_m=x,y_m=y,a_count=len(pa),b_count=len(pb),
                                     a_c=va,b_c=vb,delta_c=delta,
                                     status='SINGLE_PATCH_EACH' if delta is not None else 'NO_UNIQUE_PAIR'))
            lines.append('※同じXYの各モデルのパッチ値を比較。高さ・同一面の対応は保証しません。')
        else:
            region=_polygon(coords)
            ea=evaluate_area(vertices,canvas_crs,[a],threshold_c)
            eb=evaluate_area(vertices,canvas_crs,[b],threshold_c)
            audit.update(region_wkt=region.asWkt(12),region_area_m2=region.area(),threshold_c=threshold_c)
            lines.append('共通区域：%s｜面積 %.2fm²'%(crs.authid(),region.area()))
            for r in ('GROUND','ROOF'):
                sa=ea['results'].get(r);sb=eb['results'].get(r)
                if sa is None or sb is None:
                    lines.append(r+'：片側のパッチがありません。');continue
                va=sa['mean'];vb=sb['mean'];delta=(vb-va if va is not None and vb is not None else None)
                lines.append('%s 水平投影面積加重：A %s／B %s／平均の差 %s'%(
                    r,'%.2f℃'%va if va is not None else 'なし',
                    '%.2f℃'%vb if vb is not None else 'なし',
                    '%+.2f℃'%delta if delta is not None else '算出不可'))
                lines.append('  被覆面積：A %.2fm²／B %.2fm²（差 %+.2fm²）'%(
                    sa['union_area'],sb['union_area'],sb['union_area']-sa['union_area']))
                if sa['hot_percent'] is not None and sb['hot_percent'] is not None:
                    lines.append('  %.1f℃以上の有効面積比：A %.1f%%／B %.1f%%'%(
                        threshold_c,sa['hot_percent'],sb['hot_percent']))
                if abs(sb['union_area']-sa['union_area'])>max(0.01,0.005*region.area()):
                    lines.append('  【注意】被覆面積が異なります。平均温度の差は共通被覆だけの変化とは限りません。')
                csv_rows.append(dict(role=r,region_area_m2=region.area(),
                                     a_count=sa['count'],b_count=sb['count'],
                                     a_covered_m2=sa['union_area'],b_covered_m2=sb['union_area'],
                                     a_hot_pct=sa['hot_percent'],b_hot_pct=sb['hot_percent'],
                                     a_c=va,b_c=vb,delta_c=delta,status='SEPARATE_WEIGHTED_MEANS'))
            lines.append('※Surfaceは同一区域の各モデルで独立に面積加重平均した差です。パッチ間の一対一の温度差マップではありません。')
            lines.append('※A/Bの被覆面積が異なれば平均値の差に影響します。WALLは対象外。')
    lines.extend(['','※解析結果は読み取り専用です。値の補間・元GeoPackageの変更は行いません。'])
    return dict(report='\n'.join(lines),rows=csv_rows,graph=graph,audit=audit)

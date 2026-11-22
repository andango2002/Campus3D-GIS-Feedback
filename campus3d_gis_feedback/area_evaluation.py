# -*- coding: utf-8 -*-
"""Read-only polygon evaluation of previously computed thermal data."""
import math
from qgis.core import QgsGeometry, QgsPointXY, QgsFeatureRequest, QgsWkbTypes, Qgis
from .point_evaluation import _projected_point, _matching_time
from .point_evaluation_core import finite_number
from .area_evaluation_core import weighted_summary, ratio


def _checked_union(parts):
    if not parts:
        return None
    # Merge in batches to avoid passing hundreds of thousands of geometries
    # to a single GEOS operation. Compact the intermediate union as well.
    while len(parts)>1:
        nxt=[]
        for i in range(0,len(parts),512):
            chunk=parts[i:i+512]
            g=chunk[0] if len(chunk)==1 else QgsGeometry.unaryUnion(chunk)
            if g.isNull() or g.isEmpty() or g.lastError():
                raise ValueError('面積の重複除去に失敗しました: '+g.lastError())
            nxt.append(g)
        parts=nxt
    return parts[0]


def _require(layer, fields, geometry_type, crs):
    if not layer.isValid() or layer.crs()!=crs or layer.geometryType()!=geometry_type:
        raise ValueError('レイヤの座標系または形状が一致しません: '+layer.name())
    for f in fields:
        if layer.fields().indexFromName(f)<0:
            raise ValueError('属性がありません: '+f+' / '+layer.name())


def _surface(layer, region, stamp, threshold, crs):
    _require(layer,('surface_c','time_jst'),QgsWkbTypes.PolygonGeometry,crs)
    req=QgsFeatureRequest().setFilterRect(region.boundingBox())
    records=[]; parts=[]; missing=0; matched=0
    for f in layer.getFeatures(req):
        if not _matching_time(f,stamp): continue
        geom=f.geometry()
        if geom.isNull() or geom.isEmpty(): continue
        if not geom.intersects(region): continue
        clipped=geom.intersection(region)
        if clipped.lastError() or clipped.isNull():
            raise ValueError('ポリゴン交差に失敗しました: '+clipped.lastError())
        a=float(clipped.area())
        if not math.isfinite(a): raise ValueError('交差面積が非有限値です。')
        if a<=0: continue
        matched+=1
        t=finite_number(f['surface_c'])
        if t is None:
            missing+=1; continue
        records.append((t,a)); parts.append(clipped)
    stats=weighted_summary(records,threshold)
    union=_checked_union(parts)
    union_area=union.area() if union is not None else 0.
    stats.update(matched_count=matched,missing_count=missing,union_area=union_area,
                 overlap_area=max(0.,stats['weight']-union_area))
    return stats,union


def evaluate_area(vertices, canvas_crs, dataset, threshold):
    threshold=float(threshold)
    if not math.isfinite(threshold): raise ValueError('しきい値が無効です。')
    if len(vertices)<3 or len(vertices)>200: raise ValueError('区域の頂点は3〜200点です。')
    if any(not math.isfinite(v) for xy in vertices for v in xy): raise ValueError('無効な頂点です。')
    if len(set(vertices))<3: raise ValueError('異なる3点以上を指定してください。')
    if not dataset or not dataset[0][3]: raise ValueError('解析データがありません。')
    layer0=dataset[0][3][0]; crs=layer0.crs(); stamp=dataset[0][2]
    pts=[_projected_point(QgsPointXY(x,y),canvas_crs,layer0) for x,y in vertices]
    if pts[-1]!=pts[0]: pts.append(pts[0])
    region=QgsGeometry.fromPolygonXY([pts])
    if region.isNull() or region.isEmpty() or not region.isGeosValid():
        raise ValueError('区域が自己交差しているか無効です。頂点を戻して描き直してください。')
    area=float(region.area())
    if not math.isfinite(area) or area<0.01: raise ValueError('区域が小さすぎます（0.01 m²未満）。')
    sources={}; results={}; unions={}
    for kind,folder,ts,layers in dataset:
        if ts!=stamp: raise ValueError('解析時刻が一致しません。')
        if kind=='mrt':
            if len(layers)!=1: raise ValueError('MRTレイヤが一意でありません。')
            layer=layers[0]; _require(layer,('mrt_c','time_jst'),QgsWkbTypes.PointGeometry,crs)
            records=[]; matched=0; missing=0
            for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(region.boundingBox())):
                if not _matching_time(f,stamp): continue
                g=f.geometry()
                if g.isNull() or g.isEmpty() or not region.intersects(g): continue
                matched+=1; temp=finite_number(f['mrt_c'])
                if temp is None: missing+=1; continue
                records.append((temp,1.))
            stats=weighted_summary(records,threshold); stats.update(matched_count=matched,missing_count=missing)
            results['MRT']=stats; sources['MRT']=layer.source()
        if kind=='surface':
            for layer in layers:
                fname=layer.source().split('|',1)[0].replace('\\','/').lower()
                role=next((r for r in ('GROUND','ROOF') if fname.endswith('surface_'+r.lower()+'.gpkg')),None)
                if role is None: continue  # WALL centroid has no polygon coverage
                if role in results: raise ValueError(role+' レイヤが一意でありません。')
                stats,union=_surface(layer,region,stamp,threshold,crs)
                results[role]=stats; unions[role]=union; sources[role]=layer.source()
    if not results: raise ValueError('MRT、GROUND、ROOFの評価対象がありません。')
    union=_checked_union([g for g in unions.values() if g is not None])
    covered=min(area,max(0.,union.area() if union is not None else 0.)) if unions else None
    complete_roles=set(unions)=={'GROUND','ROOF'}
    lines=[f'面評価｜{stamp}',f'座標系：{crs.authid()} / 区域面積：{area:,.2f} m²',f'高温判定：{threshold:g} °C以上','']
    for role in ('MRT','GROUND','ROOF'):
        if role not in results:
            lines.append(f'【{role}】レイヤなし'); continue
        s=results[role]; is_surface=role!='MRT'
        lines.append(f'【{role}】'+('水平投影面積加重' if is_surface else '区域内評価点の単純統計'))
        lines.append(f'有効：{s["count"]:,} / {s["matched_count"]:,} '+('パッチ' if is_surface else '点')+f'（値なし {s["missing_count"]:,}）')
        if s['mean'] is None: lines.append('温度統計：算出不可（有効データなし）')
        else:
            lines.append('平均 %.2f / 最小 %.2f / 最大 %.2f / 標準偏差 %.2f °C' %(s['mean'],s['minimum'],s['maximum'],s['sd']))
            lines.append(f'{threshold:g} °C以上：{s["hot_percent"]:.2f} %'+('（有効パッチ交差面積比）' if is_surface else '（有効評価点数比）'))
        if is_surface:
            lines.append(f'有効交差面積合計 {s["weight"]:,.2f} m² / 重複除去 {s["union_area"]:,.2f} m²')
            if s['overlap_area']>max(0.01,1.e-6*area): lines.append(f'注意：同種パッチ投影の重複 {s["overlap_area"]:,.2f} m²。統計はパッチ面積の総和で重み付け。')
        lines.append('')
    if covered is not None:
        lines.append('【有効Surface投影被覆（'+('GROUND＋ROOF' if complete_roles else '読み込み済み役割のみ')+'・重複除去）】')
        lines.append(f'被覆 {covered:,.2f} m²（{ratio(covered,area):.2f}%） / 未被覆 {area-covered:,.2f} m²（{ratio(area-covered,area):.2f}%）')
        if not complete_roles: lines.append('注意：GROUND/ROOFの片方が未読込のため、全Surfaceの被覆率ではありません。')
    else: lines.append('Surface被覆率：算出不可（対象レイヤなし）')
    lines.extend(['','※MRTは点数比であり面積比ではありません。格子間隔や建物内除外の影響を受けます。',
                  '※GROUNDとROOFの温度集計は別々。水平投影面積であり3D表面積ではありません。WALLは対象外。',
                  '※未被覆には解析範囲外も含まれます。温度を補間・再計算しません。'])
    return dict(stamp=stamp,crs=crs.authid(),wkt=region.asWkt(12),area=area,threshold=threshold,
                results=results,sources=sources,covered=covered,complete_roles=complete_roles,report='\n'.join(lines))

# -*- coding: utf-8 -*-
"""QGIS-facing MRT (B-A) map creation. New files only; never edits source GPKGs."""
import csv
import json
import os
from datetime import datetime

from qgis.PyQt.QtCore import QVariant
from qgis.core import (Qgis, QgsProject, QgsVectorLayer, QgsFeature,
                       QgsField, QgsGeometry, QgsPointXY, QgsMarkerSymbol,
                       QgsRendererRange, QgsGraduatedSymbolRenderer, QgsVectorFileWriter)

from .comparison_delta_core import pair_mrt_records, diverging_breaks


def _extract(layer):
    expected={'point_id','time_jst','mrt_c','z_m'}
    if not layer.isValid() or not expected.issubset(set(layer.fields().names())):
        raise ValueError('MRTの基本属性が不足しています。')
    result=[]
    for f in layer.getFeatures():
        p=f.geometry()
        if p.isNull() or p.isEmpty() or not p.isGeosValid():
            raise ValueError('MRT点の形状が無効です。')
        xy=p.asPoint()
        result.append((f['point_id'],xy.x(),xy.y(),f['z_m'],f['mrt_c']))
    return result


def make_diff_layer(a, b, gpkg_path):
    """Validate first, create new GPKG + audit files, return layer & metadata.

    a/b are (kind, folder, stamp, [QgsVectorLayer]) as in ComparisonPanel.
    """
    if a[0]!='mrt' or b[0]!='mrt' or len(a[3])!=1 or len(b[3])!=1:
        raise ValueError('v0.7.1の差分マップはMRT各1レイヤ同士に対応します。')
    al, bl = a[3][0], b[3][0]
    crs=al.crs()
    if not crs.isValid() or crs.isGeographic() or crs.mapUnits()!=Qgis.DistanceUnit.Meters or crs!=bl.crs():
        raise ValueError('A/Bは同一の投影メートル座標系である必要があります。')
    path=os.path.abspath(gpkg_path)
    if not path.lower().endswith('.gpkg'):
        raise ValueError('保存先は.gpkgを指定してください。')
    basename=os.path.splitext(path)[0]
    csv_path=basename+'.csv'; report_path=basename+'_report.json'
    if any(os.path.exists(p) for p in (path,csv_path,report_path)):
        raise ValueError('同名の比較結果が既にあります。別名で保存してください（上書きしません）。')
    if not os.path.isdir(os.path.dirname(path)):
        raise ValueError('保存先フォルダが存在しません。')
    if os.path.normcase(os.path.realpath(path)) in (os.path.normcase(os.path.realpath(os.path.join(a[1],'mrt_points.gpkg'))),
                                                     os.path.normcase(os.path.realpath(os.path.join(b[1],'mrt_points.gpkg')))):
        raise ValueError('元のMRTファイルは上書きできません。')
    paired, stats=pair_mrt_records(_extract(al), _extract(bl))
    mem=QgsVectorLayer('Point?crs='+crs.authid(),'Campus3D ΔMRT (B−A)','memory')
    if not mem.isValid():raise RuntimeError('差分レイヤの作成に失敗しました。')
    fields=[QgsField('point_id',QVariant.Int),QgsField('mrt_A_c',QVariant.Double),
            QgsField('mrt_B_c',QVariant.Double),QgsField('delta_c',QVariant.Double),
            QgsField('z_A_m',QVariant.Double),QgsField('z_B_m',QVariant.Double)]
    mem.dataProvider().addAttributes(fields);mem.updateFields()
    buffer=[]
    for pid,x,y,az,bz,at,bt,delta in paired:
        ft=QgsFeature(mem.fields());ft.setAttributes([pid,at,bt,delta,az,bz])
        ft.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x,y)));buffer.append(ft)
    if not mem.dataProvider().addFeatures(buffer)[0]:
        raise RuntimeError('差分点の生成に失敗しました。')
    mem.updateExtents()
    if mem.featureCount()!=len(paired):raise RuntimeError('差分点数が一致しません。')
    cuts=diverging_breaks([p[-1] for p in paired])
    ranges=[]
    for lo,hi,color in cuts:
        symbol=QgsMarkerSymbol.createSimple({'name':'circle','color':color,
                                             'outline_style':'no','size':'1.7'})
        ranges.append(QgsRendererRange(lo,hi,symbol,('%+.1f ～ %+.1f °C'%(lo,hi))))
    mem.setRenderer(QgsGraduatedSymbolRenderer('delta_c',ranges))
    metadata={
        'schema':'Campus3D_DeltaMRT_v0.7.1','quantity':'MRT_DELTA_B_MINUS_A',
        'unit':'degree_C', 'crs':crs.authid(),
        'A':{'source':a[1],'timestamp':a[2],'layer':al.source()},
        'B':{'source':b[1],'timestamp':b[2],'layer':bl.source()},
        'statistics':stats,
        'colorbar':{'min_c':cuts[0][0],'max_c':cuts[-1][1],
                    'colors':[v[2] for v in cuts]},
        'interpretation':('B-A: negative means cooler B; positive means warmer B. Different timestamps, so not a design-only causal effect.'
                          if a[2]!=b[2] else 'B-A: negative means cooler B; positive means warmer B. Similar timestamps do not establish identical boundary conditions.'),
        'pairing':'Same point_id, XY <= 0.05 m and physical Z <= 0.05 m; no interpolation.',
        'created_local':datetime.now().isoformat(timespec='seconds')
    }
    made=[]
    try:
        options=QgsVectorFileWriter.SaveVectorOptions()
        options.driverName='GPKG';options.layerName='mrt_delta';options.fileEncoding='UTF-8'
        status=QgsVectorFileWriter.writeAsVectorFormatV3(mem,path,QgsProject.instance().transformContext(),options)
        if status[0]!=QgsVectorFileWriter.NoError:
            raise RuntimeError('差分GeoPackageの保存に失敗しました: '+str(status))
        made.append(path)
        with open(csv_path,'x',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f)
            w.writerow(['point_id','x_gis','y_gis','z_A_m','z_B_m','mrt_A_c','mrt_B_c','delta_B_minus_A_c'])
            w.writerows(paired)
        made.append(csv_path)
        with open(report_path,'x',encoding='utf-8') as f:
            json.dump(metadata,f,ensure_ascii=False,indent=2)
        made.append(report_path)
        layer=QgsVectorLayer(path+'|layername=mrt_delta','Campus3D ΔMRT B−A / %s → %s'%(a[2][:16],b[2][:16]),'ogr')
        if not layer.isValid() or layer.featureCount()!=len(paired):
            raise RuntimeError('差分GeoPackageの読込または件数照合に失敗しました。')
        layer.setRenderer(mem.renderer().clone())
        layer.setCustomProperty('Campus3D/delta',True)
        return layer, metadata, cuts
    except Exception:
        for f in reversed(made):
            try:os.remove(f)
            except OSError:pass
        raise

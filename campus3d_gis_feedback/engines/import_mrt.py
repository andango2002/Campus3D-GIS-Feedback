"""Run from QGIS Python Console: exec(open(r'PATH\\qgis_import_mrt.py', encoding='utf-8').read()).
Uses a file-picker to find gis_manifest.json; QGIS supplies the GIS functions.
"""
import csv
import json
import math
import os
from qgis.core import (QgsCoordinateReferenceSystem,QgsFeature,QgsField,QgsGeometry,
                       QgsPointXY,QgsVectorLayer,QgsVectorFileWriter,QgsProject)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QFileDialog


def run_import_mrt(manifest_path):
    if manifest_path:
        with open(manifest_path,encoding='utf-8') as f: info=json.load(f)
        if info.get('schema')!='Campus3D_GIS_Feedback_v0.1' or info.get('quantity')!='MRT':
            raise ValueError('Not a Campus3D MRT GIS Feedback manifest')
        crs=QgsCoordinateReferenceSystem(info['crs'])
        if not crs.isValid() or crs.isGeographic():
            raise ValueError('Expected a valid projected-metre EPSG CRS; not geographic degrees')
        from qgis.core import Qgis
        if crs.mapUnits()!=Qgis.DistanceUnit.Meters:
            raise ValueError('QGIS CRS map units must be metres')
        mem=QgsVectorLayer('Point?crs='+info['crs'],'Campus3D MRT '+str(info['timestamp_jst']),'memory')
        if not mem.isValid():raise ValueError('Could not create QGIS memory layer')
        fields=[QgsField('point_id',QVariant.Int),QgsField('time_jst',QVariant.String),
                QgsField('mrt_c',QVariant.Double),QgsField('z_m',QVariant.Double)]
        mem.dataProvider().addAttributes(fields);mem.updateFields()
        csv_file=os.path.join(os.path.dirname(manifest_path),info.get('csv_file','mrt_points.csv'))
        with open(csv_file,encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        if len(rows)!=int(info['point_count']):raise ValueError('CSV point count differs from manifest')
        features=[];seen=set()
        for row in rows:
            id=int(row['point_id'])
            if id in seen:raise ValueError('Duplicate MRT point ID')
            seen.add(id)
            if row['time_jst']!=info['timestamp_jst']:raise ValueError('Time mismatch in MRT CSV')
            x,y,z,mrt=(float(row[k]) for k in ('x_gis','y_gis','z_physical_m','mrt_c'))
            if not all(math.isfinite(v) for v in (x,y,z,mrt)):raise ValueError('Nonfinite MRT data')
            if abs(float(info['origin']['x'])+float(row['x_local_m'])-x)>1e-5 or abs(float(info['origin']['y'])+float(row['y_local_m'])-y)>1e-5:
                raise ValueError('CSV geographic coordinate does not match original local XY')
            ft=QgsFeature(mem.fields());ft.setAttributes([id,row['time_jst'],mrt,z]);ft.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x,y)))
            features.append(ft)
        if not mem.dataProvider().addFeatures(features)[0]:raise ValueError('Could not populate QGIS layer')
        mem.updateExtents()
        out=os.path.join(os.path.dirname(manifest_path),'mrt_points.gpkg')
        if os.path.exists(out):raise ValueError('GeoPackage already exists; export into a fresh directory')
        opts=QgsVectorFileWriter.SaveVectorOptions();opts.driverName='GPKG';opts.layerName='mrt_points';opts.fileEncoding='UTF-8'
        status=QgsVectorFileWriter.writeAsVectorFormatV3(mem,out,QgsProject.instance().transformContext(),opts)
        if status[0]!=QgsVectorFileWriter.NoError:raise RuntimeError('GeoPackage write failed: '+str(status))
        layer=QgsVectorLayer(out+'|layername=mrt_points','Campus3D MRT '+str(info['timestamp_jst']),'ogr')
        if not layer.isValid() or layer.featureCount()!=len(rows):raise ValueError('GeoPackage output validation failed')
        QgsProject.instance().addMapLayer(layer)
        print('Campus3D GIS Feedback: OK',len(rows),'points,',info['crs'],out)

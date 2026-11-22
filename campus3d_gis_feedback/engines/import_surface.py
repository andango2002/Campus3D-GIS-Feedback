"""QGIS 3.44+: Campus3D Surface Temperature GIS Feedback importer.

QGIS Python Console:
    exec(open(r'C:\\PATH\\SURFACE_tNNN\\qgis_import_surface.py',encoding='utf-8').read())
A file dialog will ask for the gis_manifest.json next to surface_patches.csv.
Outputs separate GeoPackages (GROUND / ROOF polygons, WALL centroid points).
"""
import csv
import hashlib
import json
import math
import os

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsGeometry,
    QgsPointXY, QgsVectorLayer, QgsVectorFileWriter, QgsProject, Qgis,
    QgsGraduatedSymbolRenderer, QgsRendererRange, QgsFillSymbol, QgsMarkerSymbol,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QFileDialog


def run_import_surface(manifest_path=None):
    if manifest_path is None:
        manifest_path,_=QFileDialog.getOpenFileName(
            None,'Select Campus3D Surface gis_manifest.json','','JSON (*.json)')
    if not manifest_path:return
    with open(manifest_path,'r',encoding='utf-8') as f:meta=json.load(f)
    if meta.get('schema')!='Campus3D_GIS_Feedback_Surface_v0.2' or meta.get('quantity')!='SURFACE':
        raise ValueError('This importer requires a v0.4.1 Surface Temperature manifest')
    crs_code=meta['crs']
    crs=QgsCoordinateReferenceSystem(crs_code)
    if not crs.isValid() or crs.isGeographic() or crs.mapUnits()!=Qgis.DistanceUnit.Meters:
        raise ValueError('Surface GIS Feedback requires an EPSG projected-metre CRS')
    origin_x=float(meta['origin']['x']);origin_y=float(meta['origin']['y'])
    if not all(math.isfinite(v) for v in (origin_x,origin_y)):
        raise ValueError('Invalid manifest origin')
    if int(meta.get('patch_count',0))<1 or int(meta['patch_count'])>500000:
        raise ValueError('Invalid patch count')
    directory=os.path.dirname(os.path.abspath(manifest_path))
    csv_path=os.path.join(directory,meta.get('csv_file','surface_patches.csv'))
    if os.path.dirname(os.path.abspath(csv_path))!=directory:
        raise ValueError('Manifest CSV path must be in the same folder')
    dig=hashlib.sha256()
    with open(csv_path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):dig.update(chunk)
    if dig.hexdigest()!=meta['csv_sha256']:
        raise ValueError('Surface CSV SHA-256 does not match export manifest')

    role_order=('GROUND','ROOF','WALL')
    counts={role:0 for role in role_order}
    seen=set()
    mint=float('inf');maxt=float('-inf')
    fields=meta['field_names']
    def clean(row):
        if set(row)!=set(fields) or any(v is None for v in row.values()):
            raise ValueError('Invalid/changed CSV fields')
        pid=int(row['patch_id'])
        if not 1<=pid<=500000:raise ValueError('Invalid surface patch ID')
        role=row['kind']
        if role not in counts:raise ValueError('Unexpected surface patch role')
        if row['time_jst']!=meta['timestamp_jst']:raise ValueError('Surface patch timestamp mismatch')
        nums={k:float(row[k]) for k in (
            'surface_c','area_m2','x_gis','y_gis','z_physical_m','x_local_m','y_local_m',
            'nx','ny','nz','x1_gis','y1_gis','x2_gis','y2_gis','x3_gis','y3_gis')}
        if not all(math.isfinite(v) for v in nums.values()) or nums['area_m2']<=0:
            raise ValueError('Invalid geometry, area, or temperature in surface CSV')
        if abs(origin_x+nums['x_local_m']-nums['x_gis'])>1e-5 or \
           abs(origin_y+nums['y_local_m']-nums['y_gis'])>1e-5:
            raise ValueError('Projected centre coordinate differs from QGIS origin formula')
        verts=[(nums[f'x{i}_gis'],nums[f'y{i}_gis']) for i in (1,2,3)]
        if abs(sum(v[0] for v in verts)/3.-nums['x_gis'])>.035 or \
           abs(sum(v[1] for v in verts)/3.-nums['y_gis'])>.035:
            raise ValueError('Projected surface vertices and centre do not agree')
        if role!='WALL':
            (x1,y1),(x2,y2),(x3,y3)=verts
            area2d=.5*abs((x2-x1)*(y3-y1)-(x3-x1)*(y2-y1))
            if area2d<1.e-9:raise ValueError('Degenerate ground/roof 2D polygon')
        return pid,role,nums,verts

    # Preflight all rows BEFORE creating any GeoPackages; avoid half-valid GIS files.
    with open(csv_path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=fields:raise ValueError('Unexpected CSV header')
        for row in reader:
            pid,role,nums,verts=clean(row)
            if pid in seen:raise ValueError('Duplicate surface patch ID')
            seen.add(pid);counts[role]+=1
            mint=min(mint,nums['surface_c']);maxt=max(maxt,nums['surface_c'])
    if sum(counts.values())!=int(meta['patch_count']) or counts!=meta['patch_counts']:
        raise ValueError('Manifest and surface CSV patch counts disagree')
    gpkg_paths={role:os.path.join(directory,'surface_'+role.lower()+'.gpkg')
                for role in role_order if counts[role]}
    if any(os.path.exists(p) for p in gpkg_paths.values()):
        raise ValueError('Surface GeoPackage already exists; use a fresh export folder')

    fields_out=[QgsField('patch_id',QVariant.Int),QgsField('time_jst',QVariant.String),
                QgsField('surface_c',QVariant.Double),QgsField('area_m2',QVariant.Double),
                QgsField('z_m',QVariant.Double),QgsField('source_id',QVariant.Int),
                QgsField('source_face',QVariant.Int),QgsField('nx',QVariant.Double),
                QgsField('ny',QVariant.Double),QgsField('nz',QVariant.Double)]
    added=[];made=[]
    def make_renderer(kind):
        low=mint;high=maxt
        if high-low<.001:low-=1.;high+=1.
        colors=('#2369d4','#30bfe2','#55c58b','#eee057','#ee9135','#d84c38')
        segments=[]
        for i,hexcolor in enumerate(colors):
            a=low+(high-low)*i/len(colors)
            b=low+(high-low)*(i+1)/len(colors)
            if kind=='WALL':
                symbol=QgsMarkerSymbol.createSimple({'name':'circle','color':hexcolor,
                                                      'outline_style':'no','size':'1.7'})
            else:
                symbol=QgsFillSymbol.createSimple({'color':hexcolor,
                                                    'outline_style':'no'})
            segments.append(QgsRendererRange(a,b,symbol,f'{a:.1f} - {b:.1f} C'))
        return QgsGraduatedSymbolRenderer('surface_c',segments)
    try:
        for role in role_order:
            if not counts[role]:continue
            typ='Point' if role=='WALL' else 'Polygon'
            mem=QgsVectorLayer(f'{typ}?crs={crs_code}',f'Campus3D Surface {role}','memory')
            if not mem.isValid():raise RuntimeError('Could not create QGIS '+role+' layer')
            mem.dataProvider().addAttributes(fields_out);mem.updateFields()
            with open(csv_path,encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f)
                buf=[]
                for row in reader:
                    if row['kind']!=role:continue
                    pid,_,n,points=clean(row)
                    feature=QgsFeature(mem.fields())
                    feature.setAttributes([pid,row['time_jst'],n['surface_c'],n['area_m2'],
                        n['z_physical_m'],int(row['source_id']),int(row['source_face']),
                        n['nx'],n['ny'],n['nz']])
                    if role=='WALL':
                        geom=QgsGeometry.fromPointXY(QgsPointXY(n['x_gis'],n['y_gis']))
                    else:
                        ring=[QgsPointXY(x,y) for x,y in points]
                        geom=QgsGeometry.fromPolygonXY([ring+[ring[0]]])
                    feature.setGeometry(geom);buf.append(feature)
                    if len(buf)>=2000:
                        if not mem.dataProvider().addFeatures(buf)[0]:
                            raise RuntimeError('Could not add '+role+' features')
                        buf.clear()
                if buf and not mem.dataProvider().addFeatures(buf)[0]:
                    raise RuntimeError('Could not add remaining '+role+' features')
            mem.updateExtents()
            if mem.featureCount()!=counts[role]:raise RuntimeError('GIS feature count mismatch '+role)
            opts=QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName='GPKG';opts.layerName='surface_'+role.lower();opts.fileEncoding='UTF-8'
            out=gpkg_paths[role]
            status=QgsVectorFileWriter.writeAsVectorFormatV3(mem,out,
                QgsProject.instance().transformContext(),opts)
            if status[0]!=QgsVectorFileWriter.NoError:
                raise RuntimeError('GeoPackage write failed for '+role+': '+str(status))
            made.append(out)
            display=QgsVectorLayer(out+'|layername=surface_'+role.lower(),
                                   'Campus3D Surface '+role+' '+str(meta['timestamp_jst']),'ogr')
            if not display.isValid() or display.featureCount()!=counts[role]:
                raise RuntimeError('GeoPackage verification failed for '+role)
            display.setRenderer(make_renderer(role))
            added.append(display)
        for layer in added:QgsProject.instance().addMapLayer(layer)
    except Exception:
        for path in made:
            try:os.remove(path)
            except OSError:pass
        raise
    print('Campus3D GIS Surface Feedback: OK',sum(counts.values()),
          'patches;',counts,';',crs_code,';',directory)
    print('GROUND/ROOF = projected triangular patches; WALL = centroid points.')
    print('Color range shared across all three surface layers: %.2f..%.2f C'%(mint,maxt))



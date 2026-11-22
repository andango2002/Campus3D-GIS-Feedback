"""QGIS 3.44 LTR: import a PROVISIONAL Campus3D HIP domain result.

From QGIS Python Console:
 exec(open(r'C:\\PATH\\HIP_tNNN\\qgis_import_hip.py',encoding='utf-8').read())
Select gis_manifest.json in the HIP folder.
This creates an outline polygon of the *original rectangular extent*. It does
not imply that the simulated thermal patches cover the full region.
"""
import csv
import hashlib
import json
import math
import os
from qgis.core import (Qgis,QgsCoordinateReferenceSystem,QgsFeature,QgsField,
    QgsFillSymbol,QgsGeometry,QgsPointXY,QgsProject,QgsVectorFileWriter,QgsVectorLayer)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QFileDialog


def _hash(path):
    sha=hashlib.sha256()
    with open(path,'rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):sha.update(part)
    return sha.hexdigest()


def _near(a,b):
    return math.isclose(float(a),float(b),rel_tol=2e-9,abs_tol=1e-7)


def run_import_hip(path=None):
    if path is None:
        path,_=QFileDialog.getOpenFileName(None,'Select Campus3D HIP gis_manifest.json','','JSON (*.json)')
    if not path:return
    with open(path,encoding='utf-8') as f:meta=json.load(f)
    if meta.get('schema')!='Campus3D_GIS_Feedback_HIP_v0.1' or meta.get('quantity')!='HIP':
        raise ValueError('Not a Campus3D HIP v0.1 manifest')
    if meta.get('quality')!='PROVISIONAL_DOMAIN_HIP':
        raise ValueError('HIP quality warning absent; refuse unqualified input')
    crs=QgsCoordinateReferenceSystem(meta['crs'])
    if not crs.isValid() or crs.isGeographic() or crs.mapUnits()!=Qgis.DistanceUnit.Meters:
        raise ValueError('Projected metre CRS required')
    directory=os.path.dirname(os.path.abspath(path))
    for key,digest in [('summary_file','summary_sha256'),('patch_file','patch_sha256')]:
        fn=meta[key]
        fp=os.path.abspath(os.path.join(directory,fn))
        if os.path.dirname(fp)!=directory or not os.path.isfile(fp):
            raise ValueError('HIP CSV must be present beside its manifest')
        if _hash(fp)!=meta[digest]:
            raise ValueError('HIP CSV checksum mismatch: '+fn)
    extent=meta['source_manifest_extent']
    mnx,mxx,mny,mxy=(float(extent[k]) for k in ('xmin','xmax','ymin','ymax'))
    if not all(math.isfinite(v) for v in (mnx,mxx,mny,mxy)) or mxx<=mnx or mxy<=mny:
        raise ValueError('Invalid original QGIS extent')
    area=(mxx-mnx)*(mxy-mny)
    if not _near(area,meta['plan_area_m2']):raise ValueError('Wrong HIP denominator area')
    with open(os.path.join(directory,meta['summary_file']),encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=meta['summary_fields']:raise ValueError('HIP summary header changed')
        rows=list(reader)
    if len(rows)!=4 or [r['role'] for r in rows]!=['ALL','GROUND','ROOF','WALL']:
        raise ValueError('HIP requires ALL, GROUND, ROOF and WALL summary rows')
    if sum(int(r['patch_count']) for r in rows[1:])!=int(rows[0]['patch_count']):
        raise ValueError('HIP patch counts inconsistent')
    if not _near(float(rows[0]['hip_contribution_c']),sum(float(r['hip_contribution_c']) for r in rows[1:])):
        raise ValueError('HIP component contributions inconsistent')
    air=float(meta['air_c'])
    if not math.isfinite(air) or any(not _near(r['air_c'],air) for r in rows):
        raise ValueError('Air temperature inconsistency')
    if int(rows[0]['patch_count'])!=int(meta['patch_count']):
        raise ValueError('Manifest patch count inconsistent')
    # Verify patch CSV quantities and patch identity rather than trusting the four aggregate rows.
    sums={k:{'n':0,'area':0.,'term':0.} for k in ('GROUND','ROOF','WALL')}
    seen=set()
    with open(os.path.join(directory,meta['patch_file']),encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=meta['patch_fields']:raise ValueError('HIP patch CSV header changed')
        for p in reader:
            kind=p['kind'];pid=int(p['patch_id'])
            if kind not in sums or pid<=0 or pid in seen:raise ValueError('Unknown or duplicate HIP patch')
            seen.add(pid)
            if p['timestamp_jst']!=meta['timestamp_jst']:raise ValueError('Timestamp mismatch')
            a,ts,ta,term=(float(p[k]) for k in ('area_m2','surface_c','air_c','thermal_term_c_m2'))
            x,y,z=(float(p[k]) for k in ('x_gis','y_gis','z_local_m'))
            if not all(math.isfinite(v) for v in (a,ts,ta,term,x,y,z)) or a<=0:
                raise ValueError('Invalid HIP patch numeric values')
            if not _near(ta,air) or not _near(a*(ts-air),term):
                raise ValueError('HIP patch term error')
            if not _near(float(p['delta_c']),ts-air):raise ValueError('HIP patch temperature difference mismatch')
            if x<mnx-.05 or x>mxx+.05 or y<mny-.05 or y>mxy+.05:
                raise ValueError('HIP patch outside source region')
            sums[kind]['n']+=1;sums[kind]['area']+=a;sums[kind]['term']+=term
    if len(seen)!=int(meta['patch_count']):raise ValueError('Missing HIP patch rows')
    for r in rows[1:]:
        sm=sums[r['role']]
        if sm['n']!=int(r['patch_count']) or not _near(sm['area'],r['surface_area_m2']) or not _near(sm['term'],r['thermal_term_c_m2']):
            raise ValueError('HIP per-role totals mismatch')
    if not _near(sum(sums[k]['term'] for k in sums)/area,rows[0]['hip_contribution_c']):
        raise ValueError('HIP whole-region value mismatch')
    out=os.path.join(directory,'hip_domain_provisional.gpkg')
    if os.path.exists(out):raise ValueError('Existing HIP GeoPackage; use a fresh HIP export folder')
    layer=QgsVectorLayer('Polygon?crs='+meta['crs'],'Campus3D provisional HIP domain','memory')
    if not layer.isValid():raise ValueError('Could not create QGIS HIP domain layer')
    fields=[QgsField('time_jst',QVariant.String),QgsField('hip_c',QVariant.Double),
            QgsField('ground_c',QVariant.Double),QgsField('roof_c',QVariant.Double),
            QgsField('wall_c',QVariant.Double),QgsField('air_c',QVariant.Double),
            QgsField('plan_m2',QVariant.Double),QgsField('n_patches',QVariant.Int),
            QgsField('status',QVariant.String)]
    layer.dataProvider().addAttributes(fields);layer.updateFields()
    feature=QgsFeature(layer.fields())
    ring=[QgsPointXY(mnx,mny),QgsPointXY(mxx,mny),QgsPointXY(mxx,mxy),QgsPointXY(mnx,mxy),QgsPointXY(mnx,mny)]
    feature.setGeometry(QgsGeometry.fromPolygonXY([ring]))
    feature.setAttributes([meta['timestamp_jst'],float(rows[0]['hip_contribution_c']),
      float(rows[1]['hip_contribution_c']),float(rows[2]['hip_contribution_c']),
      float(rows[3]['hip_contribution_c']),air,area,int(rows[0]['patch_count']),
      'PROVISIONAL - check footprint and gaps'])
    if not layer.dataProvider().addFeatures([feature])[0]:raise ValueError('Failed to create HIP feature')
    layer.updateExtents()
    opts=QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName='GPKG';opts.layerName='hip_domain_provisional';opts.fileEncoding='UTF-8'
    status=QgsVectorFileWriter.writeAsVectorFormatV3(layer,out,QgsProject.instance().transformContext(),opts)
    if status[0]!=QgsVectorFileWriter.NoError:
        raise RuntimeError('HIP GeoPackage writing failed: '+str(status))
    gpkg=QgsVectorLayer(out+'|layername=hip_domain_provisional',
                        'Campus3D HIP PROVISIONAL '+str(meta['timestamp_jst']),'ogr')
    if not gpkg.isValid() or gpkg.featureCount()!=1:
        raise ValueError('HIP GeoPackage verification failed')
    # Outline only; an opaque polygon would mask the previously imported Surface T layers.
    gpkg.renderer().setSymbol(QgsFillSymbol.createSimple({
        'style':'no', 'outline_style':'dash', 'outline_color':'190,25,190,255',
        'outline_width':'0.8', 'outline_width_unit':'MM'}))
    QgsProject.instance().addMapLayer(gpkg)
    print('Campus3D PROVISIONAL HIP: %.4f C; Ta %.2f C; A_plan %.2f m2; %d patches; %s'%(
        float(rows[0]['hip_contribution_c']),air,area,len(seen),meta['crs']))
    print('WARNING: rectangular footprint, exposed UNDERSIDE/tree canopy exclusions, and white gaps NOT validated.')
    print('GeoPackage:',out)


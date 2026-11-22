"""Campus3D Regional HIP v0.1 (QGIS 3.44 LTR, run from Python Console).

Read-only for Surface/HIP/Coverage exports and source zone layer.
Calculates clipped GROUND/ROOF 3D area terms; assigns WALL to centroid region.

Run in QGIS Python Console:
 exec(open(r'C:\\path\\qgis_regional_hip.py',encoding='utf-8').read())
"""
import csv
import glob
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import datetime

SCHEMA_SURFACE = 'Campus3D_GIS_Feedback_Surface_v0.2'
SCHEMA_HIP = 'Campus3D_GIS_Feedback_HIP_v0.1'
ROLES = ('GROUND', 'ROOF', 'WALL')
MAX_ZONES = 3000
MAX_PATCHES = 500000
MIN_COVERAGE_PCT = 98.0


def finite(value, name):
    try:
        x = float(value)
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError('Invalid '+name) from e
    if not math.isfinite(x):
        raise ValueError('Nonfinite '+name)
    return x


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as fp:
        for b in iter(lambda: fp.read(1024*1024),b''):
            h.update(b)
    return h.hexdigest()


def within_folder(folder, name):
    base=os.path.abspath(folder)
    out=os.path.abspath(os.path.join(base,str(name)))
    if os.path.dirname(out)!=base or not os.path.isfile(out):
        raise ValueError('Referenced input missing or outside export folder: '+str(name))
    return out


def extent_box(src):
    keys=('xmin','xmax','ymin','ymax')
    result={k:finite(src[k],k) for k in keys}
    if result['xmax']<=result['xmin'] or result['ymax']<=result['ymin']:
        raise ValueError('Invalid original source rectangle')
    return result


def equal_extent(a,b,tol=.001):
    a,b=extent_box(a),extent_box(b)
    return all(abs(a[k]-b[k])<=tol for k in a)


def allocated_patch_area(area_3d, fraction_2d):
    a=finite(area_3d,'3D area'); f=finite(fraction_2d,'2D overlap fraction')
    if a<=0 or f < -1e-8 or f > 1+1e-7:
        raise ValueError('Invalid clipped 2D fraction / 3D surface area')
    return a*max(0.0,min(1.0,f))


def hip_c(terms, plan_area):
    a=finite(plan_area,'horizontal area')
    if a<=0: raise ValueError('Region plan area must be positive')
    return math.fsum(terms)/a


def coverage_stats(counts):
    """2m center-sample raster counts for 0 none, 1 ground, 2 roof, 3 both."""
    n=sum(int(counts.get(k,0)) for k in (0,1,2,3))
    if n<=0: return None
    if any(int(counts.get(k,0))<0 for k in (0,1,2,3)):
        raise ValueError('Negative coverage sample count')
    covered=(n-int(counts.get(0,0)))*100.0/n
    return dict(samples=n,covered_pct=covered,hole_pct=100.0-covered,
                overlap_pct=100.0*int(counts.get(3,0))/n)


def quality_label(cov):
    if cov is None or cov['samples']<25:return 'REVIEW_LOW_SAMPLE'
    if cov['covered_pct']<MIN_COVERAGE_PCT:return 'REVIEW_COVERAGE'
    return 'PROVISIONAL'


def verify_manifests(sm,hm):
    if sm.get('schema')!=SCHEMA_SURFACE or sm.get('quantity')!='SURFACE':
        raise ValueError('Select SURFACE_tNNN/gis_manifest.json')
    if hm.get('schema')!=SCHEMA_HIP or hm.get('quantity')!='HIP':
        raise ValueError('HIP manifest invalid')
    for k in ('crs','timestamp_jst','patch_generation_token'):
        if not sm.get(k) or sm[k]!=hm.get(k):
            raise ValueError('Surface/HIP mismatch: '+k)
    if not equal_extent(sm['source_manifest_extent'],hm['source_manifest_extent']):
        raise ValueError('Surface/HIP original GIS extent differs')
    if int(sm['patch_count'])!=int(hm['patch_count']):
        raise ValueError('Surface/HIP total patch count differs')
    ta=finite(hm['air_c'],'saved HIP air_c')
    area=finite(hm['plan_area_m2'],'HIP plan_area_m2')
    e=extent_box(sm['source_manifest_extent'])
    actual=(e['xmax']-e['xmin'])*(e['ymax']-e['ymin'])
    if abs(area-actual)>max(.05,actual*1e-8):
        raise ValueError('HIP plan area differs from source extent')
    return ta,actual


def load_verified_sources(surface_manifest):
    sm_path=os.path.abspath(surface_manifest)
    base=os.path.dirname(sm_path)
    with open(sm_path,'r',encoding='utf-8') as f:sm=json.load(f)
    if sm.get('schema')!=SCHEMA_SURFACE:raise ValueError('Select the SURFACE manifest')
    candidates=[]
    parent=os.path.dirname(base)
    for p in glob.glob(os.path.join(parent,'HIP_t*','gis_manifest.json')):
        try:
            with open(p,encoding='utf-8') as f:m=json.load(f)
            if (m.get('schema')==SCHEMA_HIP and
                all(sm.get(k)==m.get(k) for k in ('crs','timestamp_jst','patch_generation_token')) and
                equal_extent(sm['source_manifest_extent'],m['source_manifest_extent'])):
                candidates.append((p,m))
        except (ValueError,TypeError,KeyError,OSError):
            continue
    if len(candidates)!=1:
        raise ValueError('Exactly one matching HIP_t* export is required; found %d. Move duplicates aside or use a fresh export folder.'%len(candidates))
    hip_path,hm=candidates[0]
    air,domain_area=verify_manifests(sm,hm)
    csv_path=within_folder(base,sm.get('csv_file','surface_patches.csv'))
    hip_base=os.path.dirname(hip_path)
    patch_path=within_folder(hip_base,hm.get('patch_file','hip_patch_terms.csv'))
    summary_path=within_folder(hip_base,hm.get('summary_file','hip_summary.csv'))
    if sha256(csv_path)!=sm.get('csv_sha256') or sha256(patch_path)!=hm.get('patch_sha256') or sha256(summary_path)!=hm.get('summary_sha256'):
        raise ValueError('Surface/HIP source SHA-256 mismatch')
    if not 1<=int(sm['patch_count'])<=MAX_PATCHES:
        raise ValueError('Unexpected source patch count')
    indexed={};counts={role:0 for role in ROLES}
    with open(csv_path,encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f)
        if rd.fieldnames!=sm.get('field_names'):
            raise ValueError('Surface CSV field list changed')
        for row in rd:
            pid=int(row['patch_id']);role=row['kind']
            if not 1<=pid<=MAX_PATCHES or pid in indexed or role not in ROLES:
                raise ValueError('Duplicate/invalid source patch ID or role')
            if row['time_jst']!=sm['timestamp_jst']:
                raise ValueError('Surface CSV timestamp differs')
            temp=finite(row['surface_c'],'surface temperature')
            area=finite(row['area_m2'],'3D patch area')
            x=finite(row['x_gis'],'surface X');y=finite(row['y_gis'],'surface Y')
            if area<=0:raise ValueError('Nonpositive patch area')
            indexed[pid]=(role,temp,area,x,y)
            counts[role]+=1
    if len(indexed)!=int(sm['patch_count']) or counts!=sm['patch_counts']:
        raise ValueError('Surface patch counts do not match manifest')
    seen=set();terms=[]
    with open(patch_path,encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f)
        if rd.fieldnames!=hm.get('patch_fields'):
            raise ValueError('HIP patch CSV fields changed')
        for row in rd:
            pid=int(row['patch_id'])
            if pid not in indexed or pid in seen:
                raise ValueError('HIP patch set has missing/duplicate IDs')
            seen.add(pid)
            role,temp,area,x,y=indexed[pid]
            if (row['kind']!=role or row['timestamp_jst']!=sm['timestamp_jst'] or
                abs(finite(row['surface_c'],'HIP temperature')-temp)>1e-5 or
                abs(finite(row['area_m2'],'HIP area')-area)>max(1e-4,area*1e-7) or
                abs(finite(row['x_gis'],'HIP X')-x)>.01 or
                abs(finite(row['y_gis'],'HIP Y')-y)>.01 or
                abs(finite(row['air_c'],'HIP air')-air)>1e-5):
                raise ValueError('Surface/HIP per-patch data mismatch ID '+str(pid))
            thermal=area*(temp-air)
            if abs(finite(row['thermal_term_c_m2'],'HIP thermal term')-thermal)>max(.002,abs(thermal)*1e-7):
                raise ValueError('HIP thermal term differs, ID '+str(pid))
            terms.append(thermal)
    if len(seen)!=len(indexed):raise ValueError('HIP missing source patches')
    with open(summary_path,encoding='utf-8-sig',newline='') as f:
        summary={row['role']:row for row in csv.DictReader(f)}
    if 'ALL' not in summary or any(k not in summary for k in ROLES):
        raise ValueError('Missing domain HIP summary fields')
    domain_hip=finite(summary['ALL']['hip_contribution_c'],'HIP summary')
    computed=hip_c(terms,domain_area)
    if abs(computed-domain_hip)>1e-5:
        raise ValueError('Original HIP does not reconcile with per-patch terms')
    return sm,hm,indexed,counts,domain_hip,air,domain_area,base,hip_path


def find_verified_audit(base,sm):
    results=[]
    for p in sorted(glob.glob(os.path.join(base,'Coverage_Audit_*','coverage_audit.json')),reverse=True):
        try:
            with open(p,encoding='utf-8') as f:a=json.load(f)
            if (a.get('schema')!='Campus3D_Coverage_Audit_v0.1' or
                a.get('timestamp_jst')!=sm['timestamp_jst'] or
                a.get('crs')!=sm['crs'] or
                not equal_extent(a['source_extent'],sm['source_manifest_extent']) or
                a.get('surface_counts')!=sm['patch_counts'] or
                os.path.normcase(os.path.normpath(a.get('surface_manifest','')))!=
                os.path.normcase(os.path.normpath(os.path.join(base,'gis_manifest.json')))):
                continue
            raster=os.path.join(os.path.dirname(p),'coverage_mask_2m.tif')
            if os.path.isfile(raster):results.append((p,raster))
        except (ValueError,KeyError,TypeError,OSError):
            continue
    if not results:
        raise ValueError('Matching Coverage_Audit_*/coverage_mask_2m.tif not found. Run Campus3D GIS Coverage Audit v0.1 first.')
    return results[0]


def run_regional_hip(sm_path=None):
    from qgis.core import (QgsCoordinateReferenceSystem,QgsCoordinateTransform,QgsFeature,
        QgsField,QgsGeometry,QgsPointXY,QgsProject,QgsRectangle,QgsSpatialIndex,
        QgsVectorLayer,QgsVectorFileWriter,QgsWkbTypes,Qgis)
    from qgis.PyQt.QtCore import QVariant,Qt,QCoreApplication
    from qgis.PyQt.QtWidgets import QFileDialog,QInputDialog,QProgressDialog,QMessageBox
    from qgis.PyQt.QtGui import QColor
    from qgis.core import QgsGraduatedSymbolRenderer,QgsRendererRange,QgsFillSymbol
    from osgeo import gdal,ogr,osr
    import numpy as np

    if sm_path is None:
        sm_path,_=QFileDialog.getOpenFileName(None,'Select SURFACE_tNNN/gis_manifest.json','','JSON (*.json)')
    if not sm_path:return
    print('Campus3D Regional HIP: validating source files and exact HIP patch match ...')
    sm,hm,indexed,expected,domain_hip,air,domain_area,base,hip_path=load_verified_sources(sm_path)
    audit_path,mask_path=find_verified_audit(base,sm)
    crs=QgsCoordinateReferenceSystem(sm['crs'])
    if not crs.isValid() or crs.isGeographic() or crs.mapUnits()!=Qgis.DistanceUnit.Meters:
        raise ValueError('Projected metre CRS required')
    ext=extent_box(sm['source_manifest_extent'])
    domain_geom=QgsGeometry.fromRect(QgsRectangle(ext['xmin'],ext['ymin'],ext['xmax'],ext['ymax']))

    existing=[]
    for lyr in QgsProject.instance().mapLayers().values():
        if isinstance(lyr,QgsVectorLayer) and lyr.isValid() and lyr.geometryType()==QgsWkbTypes.PolygonGeometry:
            if lyr.featureCount()>0 and lyr.featureCount()<=MAX_ZONES:
                existing.append(lyr)
    choices=['100 m grid (TEST, not real blocks)','200 m grid (TEST, not real blocks)']
    choices += ['Existing polygons: '+x.name()+' ['+x.id()[:8]+']' for x in existing]
    active=iface.activeLayer()
    default=next((i+2 for i,l in enumerate(existing) if l==active),0)
    choice,ok=QInputDialog.getItem(None,'Campus3D Regional HIP','Select analysis polygons:',choices,default,False)
    if not ok:return
    selected=choices.index(choice)
    zones=[]
    if selected in (0,1):
        step=100 if selected==0 else 200
        c=0;y=ext['ymin']
        while y<ext['ymax']-1e-7:
            yy=min(ext['ymax'],y+step);x=ext['xmin']
            while x<ext['xmax']-1e-7:
                xx=min(ext['xmax'],x+step);c+=1
                zones.append((f'GRID_{c:04d}',QgsGeometry.fromRect(QgsRectangle(x,y,xx,yy))))
                x=xx
            y=yy
        zone_source=f'{step}m analysis grid (not cadastral blocks)'
        is_grid=True
    else:
        layer=existing[selected-2]
        fields=[f.name() for f in layer.fields()]
        options=['(feature ID)']+fields
        guess=next((i+1 for i,n in enumerate(fields) if n.lower() in ('name','district','地区名','用途地域','名称','地域名')),0)
        label_field,ok=QInputDialog.getItem(None,'Campus3D Regional HIP','Region name field:',options,guess,False)
        if not ok:return
        transfer=QgsCoordinateTransform(layer.crs(),crs,QgsProject.instance())
        for feat in layer.getFeatures():
            geom=QgsGeometry(feat.geometry())
            if geom.isEmpty() or geom.isNull():continue
            if not geom.isGeosValid():
                raise ValueError('Invalid zone geometry at source feature '+str(feat.id())+'; repair before running')
            geom.transform(transfer)
            clipped=geom.intersection(domain_geom)
            if clipped.isEmpty() or clipped.isNull() or clipped.area()<.001:continue
            if QgsWkbTypes.geometryType(clipped.wkbType())!=QgsWkbTypes.PolygonGeometry:
                raise ValueError('Zone intersection produced nonpolygon geometry')
            label=str(feat.id()) if label_field=='(feature ID)' else str(feat[label_field])
            zones.append((label[:200],clipped))
        zone_source='Selected QGIS polygon layer: '+layer.name()+'; clipped to original source rectangle'
        is_grid=False
    if not zones or len(zones)>MAX_ZONES:
        raise ValueError('No valid zones or more than %d zones'%MAX_ZONES)
    print('Zones:',len(zones),'Mode:',zone_source)
    # Index regions to avoid O(patches*regions) overlays. Check overlaps by true area,
    # not bounding boxes (which are only a candidate filter).
    region_index=QgsSpatialIndex();region_geoms={};region_names={}; region_areas={}
    for idx,(label,geom) in enumerate(zones,1):
        area=geom.area()
        if not math.isfinite(area) or area<=0:raise ValueError('Invalid zone area')
        f=QgsFeature();f.setId(idx);f.setGeometry(geom)
        region_index.addFeature(f)
        region_geoms[idx]=geom;region_names[idx]=label;region_areas[idx]=area
    for i,g in region_geoms.items():
        for j in region_index.intersects(g.boundingBox()):
            if j<=i:continue
            h=region_geoms[j]
            if g.intersects(h):
                overlapping=g.intersection(h).area()
                if overlapping>max(0.005,1e-8*min(region_areas[i],region_areas[j])):
                    raise ValueError(f'Regions {i} and {j} overlap by {overlapping:.3f} m2: refuse duplicate HIP allocation')

    progress=QProgressDialog('Computing regional HIP from Surface patches ...','Cancel',0,sum(expected.values()),None)
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(1000)
    term={i:{r:[] for r in ROLES} for i in region_geoms}
    region_area3d={i:{r:[] for r in ROLES} for i in region_geoms}
    assigned_n={i:{r:0 for r in ROLES} for i in region_geoms}
    allocated_total=[]; processed=0; wall_boundary=0; source_area={r:0.0 for r in ROLES};seen_gpkg=set()
    try:
        for role in ROLES:
            gpkg=os.path.join(base,'surface_'+role.lower()+'.gpkg')
            if not os.path.isfile(gpkg):
                raise ValueError('Required Surface GPKG missing: '+gpkg+' (run qgis_import_surface.py first)')
            source=QgsVectorLayer(gpkg+'|layername=surface_'+role.lower(), 'Source '+role, 'ogr')
            if not source.isValid() or source.featureCount()!=expected[role] or source.crs()!=crs:
                raise ValueError('Source %s GPKG count/CRS mismatch'%role)
            for f in source.getFeatures():
                pid=int(f['patch_id']);record=indexed.get(pid)
                if record is None or record[0]!=role or pid in seen_gpkg:
                    raise ValueError('GPKG patch ID/role duplicated or differs from CSV: '+str(pid))
                seen_gpkg.add(pid)
                _,ts,area3d,xx,yy=record
                if abs(finite(f['area_m2'],'GPKG area')-area3d)>max(.0001,area3d*1e-7) or abs(finite(f['surface_c'],'GPKG temperature')-ts)>1e-5:
                    raise ValueError('GPKG numerical attributes differ from validated CSV')
                geom=f.geometry()
                if geom.isEmpty() or geom.isNull():raise ValueError('Empty source geometry '+str(pid))
                center=geom.asPoint() if role=='WALL' else geom.centroid().asPoint()
                if abs(center.x()-xx)>.04 or abs(center.y()-yy)>.04:
                    raise ValueError('Source GPKG geometry / CSV centre differs, patch '+str(pid))
                source_area[role]+=area3d
                candidates=region_index.intersects(geom.boundingBox())
                if role=='WALL':
                    if QgsWkbTypes.geometryType(geom.wkbType())!=QgsWkbTypes.PointGeometry:
                        raise ValueError('Expected WALL centroid points')
                    hits=[r for r in candidates if region_geoms[r].intersects(geom)]
                    if hits:
                        if len(hits)>1:wall_boundary+=1
                        i=min(hits)  # deterministic boundary tie, do not double count
                        term[i][role].append(area3d*(ts-air))
                        region_area3d[i][role].append(area3d)
                        assigned_n[i][role]+=1
                        allocated_total.append(area3d*(ts-air))
                else:
                    if QgsWkbTypes.geometryType(geom.wkbType())!=QgsWkbTypes.PolygonGeometry:
                        raise ValueError('Expected polygon surface '+role)
                    projected=geom.area()
                    if projected<=1e-9 or projected>area3d*1.025+0.02:
                        raise ValueError('Invalid horizontal projection area for patch '+str(pid))
                    sum_frac=0.0
                    for i in candidates:
                        z=region_geoms[i]
                        if not geom.intersects(z):continue
                        if z.contains(geom):
                            frac=1.0
                        else:
                            intersection=geom.intersection(z)
                            frac=(intersection.area()/projected if not intersection.isEmpty() else 0.0)
                        if frac<1e-12:continue
                        clipped3d=allocated_patch_area(area3d,frac)
                        sum_frac+=frac
                        term[i][role].append(clipped3d*(ts-air))
                        region_area3d[i][role].append(clipped3d)
                        assigned_n[i][role]+=1
                        allocated_total.append(clipped3d*(ts-air))
                    if sum_frac>1+1e-5:
                        raise ValueError('Double-counted overlap for patch '+str(pid))
                processed+=1
                if processed%1500==0:
                    progress.setValue(processed)
                    if processed%15000==0:print('Regional HIP progress:',processed,'/',sum(expected.values()))
                    QCoreApplication.processEvents()
                    if progress.wasCanceled():raise RuntimeError('Regional HIP canceled; no files created')
            print('Processed:',role,expected[role])
        if len(seen_gpkg)!=len(indexed):
            raise ValueError('GPKG patch IDs incomplete relative to Source Surface CSV')
        progress.setValue(sum(expected.values()))
    finally:
        progress.close()

    domain_recovered=hip_c(allocated_total,domain_area)
    if is_grid and abs(domain_recovered-domain_hip)>max(1e-4,0.0003*abs(domain_hip)):
        raise ValueError('100/200m grid sum != original domain HIP: %.7f vs %.7f C (do not trust result)'%(domain_recovered,domain_hip))
    print('Regional terms computed. HIP domain:',domain_hip,'allocated:',domain_recovered)

    # Coverage by 2m raster cell-center sampling, not a polygon union proof.
    audit=gdal.Open(mask_path,gdal.GA_ReadOnly)
    if audit is None or audit.RasterCount!=1:raise ValueError('Cannot read Coverage Audit raster')
    if audit.RasterXSize*audit.RasterYSize>15000000:
        raise ValueError('Coverage raster too large for local regional aggregation')
    cover=audit.GetRasterBand(1).ReadAsArray()
    if cover.dtype.kind not in 'ui' or not np.all(np.isin(cover,[0,1,2,3,255])):
        raise ValueError('Invalid coverage raster categories')
    gt=audit.GetGeoTransform()
    if (abs(gt[0]-ext['xmin'])>1e-5 or abs(gt[3]-ext['ymax'])>1e-5 or
        gt[1]<=0 or abs(gt[2])>1e-9 or abs(gt[4])>1e-9 or abs(gt[1]+gt[5])>1e-9):
        raise ValueError('Coverage raster does not align with source extent')
    audit_srs=osr.SpatialReference();audit_srs.ImportFromWkt(audit.GetProjection())
    required_srs=osr.SpatialReference();required_srs.SetFromUserInput(sm['crs'])
    if not audit_srs.IsSame(required_srs):raise ValueError('Audit raster CRS differs')
    zone_raster=gdal.GetDriverByName('MEM').Create('',audit.RasterXSize,audit.RasterYSize,1,gdal.GDT_Int32)
    zone_raster.SetGeoTransform(gt);zone_raster.SetProjection(audit.GetProjection())
    zone_raster.GetRasterBand(1).Fill(0)
    ds=ogr.GetDriverByName('Memory').CreateDataSource('')
    ol=ds.CreateLayer('zones',required_srs,ogr.wkbMultiPolygon)
    ol.CreateField(ogr.FieldDefn('zone_id',ogr.OFTInteger))
    for i,geom in region_geoms.items():
        g=ogr.CreateGeometryFromWkb(bytes(geom.asWkb()))
        if g.GetGeometryType()!=ogr.wkbMultiPolygon:
            g=ogr.ForceToMultiPolygon(g)
        if g is None:raise ValueError('Zone geometry cannot be converted to multipolygon')
        ff=ogr.Feature(ol.GetLayerDefn());ff.SetField('zone_id',i);ff.SetGeometry(g)
        if ol.CreateFeature(ff)!=0:raise ValueError('Could not rasterize region')
        ff=None
    if gdal.RasterizeLayer(zone_raster,[1],ol,options=['ATTRIBUTE=zone_id'])!=0:
        raise ValueError('Failed to sample region coverage raster')
    zone_ids=zone_raster.GetRasterBand(1).ReadAsArray()
    ok=(zone_ids>0)&(cover!=255)
    samples={}
    counts_raster={}
    for i in region_geoms:
        mask=ok&(zone_ids==i)
        vals=np.bincount(cover[mask].astype(np.int64),minlength=4) if np.any(mask) else np.zeros(4,dtype=np.int64)
        counts_raster[i]={k:int(vals[k]) for k in range(4)}
        samples[i]=coverage_stats(counts_raster[i])
    del zone_raster,ds,ol,audit

    # Region output is created only after every input and calculation validated.
    result_rows=[];quality_counts=defaultdict(int)
    for i in sorted(region_geoms):
        a=region_areas[i];parts={r:hip_c(term[i][r],a) for r in ROLES}
        total=math.fsum(parts.values())
        cov=samples[i];q=quality_label(cov);quality_counts[q]+=1
        row={'region_id':i,'region_name':region_names[i],'area_m2':a,
             'hip_c':total,'ground_c':parts['GROUND'],'roof_c':parts['ROOF'],'wall_c':parts['WALL'],
             'cover_pct':cov['covered_pct'] if cov else None,
             'hole_pct':cov['hole_pct'] if cov else None,
             'overlap_pct':cov['overlap_pct'] if cov else None,
             'cover_px':cov['samples'] if cov else 0,
             'n_ground':assigned_n[i]['GROUND'],'n_roof':assigned_n[i]['ROOF'],
             'n_wall':assigned_n[i]['WALL'],
             'surf_ground_m2':math.fsum(region_area3d[i]['GROUND']),
             'surf_roof_m2':math.fsum(region_area3d[i]['ROOF']),
             'surf_wall_m2':math.fsum(region_area3d[i]['WALL']),
             'quality':q}
        result_rows.append(row)
    out_base=os.path.join(base,'Regional_HIP_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    if os.path.exists(out_base):raise ValueError('Output directory collision')
    os.makedirs(out_base)
    csv_path=os.path.join(out_base,'regional_hip.csv')
    gpkg_path=os.path.join(out_base,'regional_hip_provisional.gpkg')
    meta_path=os.path.join(out_base,'regional_hip_manifest.json')
    text_path=os.path.join(out_base,'regional_hip_report.txt')
    fields_order=list(result_rows[0].keys())
    # output fields validated before writing; disk write independent of source datasets
    try:
        with open(csv_path,'w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields_order,lineterminator='\n')
            writer.writeheader();writer.writerows(result_rows)
        out_layer=QgsVectorLayer('MultiPolygon?crs='+sm['crs'],'Campus3D Regional HIP PROVISIONAL','memory')
        flds=[QgsField(name,QVariant.Int if name in ('region_id','cover_px','n_ground','n_roof','n_wall') else
                       QVariant.Double if name in ('area_m2','hip_c','ground_c','roof_c','wall_c','cover_pct','hole_pct','overlap_pct','surf_ground_m2','surf_roof_m2','surf_wall_m2')
                       else QVariant.String) for name in fields_order]
        out_layer.dataProvider().addAttributes(flds);out_layer.updateFields()
        batch=[]
        for row in result_rows:
            geom=QgsGeometry(region_geoms[row['region_id']]);geom.convertToMultiType()
            feat=QgsFeature(out_layer.fields())
            feat.setGeometry(geom)
            feat.setAttributes([row[k] for k in fields_order])
            batch.append(feat)
        if not out_layer.dataProvider().addFeatures(batch)[0]:
            raise ValueError('Could not populate output region layer')
        out_layer.updateExtents()
        opts=QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName='GPKG';opts.layerName='regional_hip';opts.fileEncoding='UTF-8'
        result=QgsVectorFileWriter.writeAsVectorFormatV3(out_layer,gpkg_path,
                QgsProject.instance().transformContext(),opts)
        if result[0]!=QgsVectorFileWriter.NoError:
            raise RuntimeError('Could not write GeoPackage '+str(result))
        loaded=QgsVectorLayer(gpkg_path+'|layername=regional_hip',
             'Campus3D Regional HIP PROVISIONAL '+str(sm['timestamp_jst']),'ogr')
        if not loaded.isValid() or loaded.featureCount()!=len(result_rows):
            raise ValueError('Output GeoPackage geometry/count validation failed')
        vals=[r['hip_c'] for r in result_rows];lo,hi=min(vals),max(vals)
        if abs(hi-lo)<1e-10:lo-=1;hi+=1
        colors=['#2c7bb6','#5ecbd4','#b1db8b','#f4dd61','#ee9250','#cc3b37']
        renderer_ranges=[]
        for j,c in enumerate(colors):
            low=lo+(hi-lo)*j/len(colors);high=lo+(hi-lo)*(j+1)/len(colors)
            symbol=QgsFillSymbol.createSimple({'color':c,'outline_color':'30,30,30,180','outline_width':'0.15'})
            renderer_ranges.append(QgsRendererRange(low,high,symbol,'%.2f – %.2f C'%(low,high)))
        loaded.setRenderer(QgsGraduatedSymbolRenderer('hip_c',renderer_ranges))
        with open(meta_path,'w',encoding='utf-8') as f:
            json.dump({'schema':'Campus3D_Regional_HIP_v0.1','quality':'PROVISIONAL',
                'timestamp_jst':sm['timestamp_jst'],'crs':sm['crs'],'patch_generation_token':sm['patch_generation_token'],
                'source_surface_manifest':sm_path,'source_surface_sha256':sm['csv_sha256'],
                'source_hip_manifest':hip_path,
                'source_hip_patch_sha256':hm['patch_sha256'],
                'source_coverage_audit':audit_path,'grid_or_layer':zone_source,
                'zone_count':len(result_rows),'air_c':air,'domain_hip_c':domain_hip,
                'allocated_domain_equivalent_hip_c':domain_recovered,
                'full_domain_partition':is_grid,'shared_hole_warning_threshold_pct':100-MIN_COVERAGE_PCT,
                'wall_method':'wall centroid assigned to a single region; approximate at boundaries',
                'ground_roof_method':'3D patch area scaled by fraction of 2D projected triangle intersecting zone',
                'coverage_method':'2m audit raster cell-centre sampling, approximate, not area union proof',
                'limitations':['Unclassified or missing ground coverage not imputed','Trees and undersides excluded',
                'Regions are planar projected polygons','Walls spanning borders are centroid-assigned','HIP is not convective heat flux',
                'Results depend on source coverage and model choices'],
                'csv_sha256':sha256(csv_path)},f,ensure_ascii=False,indent=2)
        with open(text_path,'w',encoding='utf-8') as f:
            f.write('Campus3D Regional HIP v0.1 — PROVISIONAL\n')
            f.write('Time: %s | CRS: %s | Ta: %.6f C\n'%(sm['timestamp_jst'],sm['crs'],air))
            f.write('Mode: %s, %d regions\n'%(zone_source,len(result_rows)))
            f.write('Source domain HIP: %.8f C, allocated domain-equivalent HIP: %.8f C\n'%(domain_hip,domain_recovered))
            f.write('Surface patches verified: %d (%s)\n'%(sum(expected.values()),expected))
            f.write('Region quality: %s\n'%dict(quality_counts))
            f.write('Wall-centroid tie count: %d\n'%wall_boundary)
            f.write('NOTE: 2m pixel coverage is approximate; holes are not numerically filled.\n')
            f.write('NOTE: Ground/Roof by clipped XY fraction of actual 3D area; walls centroid assigned.\n')
            f.write('NOTE: If any region has REVIEW_COVERAGE, assess source gaps before research use.\n')
            f.write('Source inputs and existing QGIS layers were not modified.\n')
        QgsProject.instance().addMapLayer(loaded)
    except Exception:
        # Do not remove partially written result: user may want diagnostic; inputs remain untouched.
        print('Partial result folder (if any):',out_base)
        raise
    print('Campus3D REGIONAL HIP: %d zones (%s)'%(len(result_rows),zone_source))
    print('Domain HIP: %.6f C; allocated domain-equivalent: %.6f C'%(domain_hip,domain_recovered))
    print('Coverage quality:',dict(quality_counts))
    print('Output:',out_base)
    QMessageBox.information(None,'Campus3D Regional HIP',
        '%d zones exported.\nDomain HIP: %.5f C\nReview required: %d zones\n\n%s'%(
          len(result_rows),domain_hip,sum(v for k,v in quality_counts.items() if k!='PROVISIONAL'),out_base))



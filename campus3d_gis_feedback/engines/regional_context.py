"""Campus3D Regional HIP Context v0.2.1 BETA — QGIS 3.44 Python console.

Augment a verified Regional HIP v0.1 layer with physical surface indicators,
optional *explicitly selected* footprint/landcover/road geometries, and descriptive
HIP associations. This is read-only for existing simulation data and project layers.

QGIS Python console:
 exec(open(r'C:\\folder\\qgis_regional_hip_context.py',encoding='utf-8').read())
"""
import csv
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import datetime

REGIONAL_SCHEMA = 'Campus3D_Regional_HIP_v0.1'
CONTEXT_SCHEMA = 'Campus3D_Regional_HIP_Context_v0.2'
MAX_REGIONS = 3000
MAX_SOURCE_FEATURES = 250000
MEASURES = ('roof_surf_ratio','wall_surf_ratio','ground_surf_ratio',
            'building_cover_pct','road_cover_pct','green_cover_pct','road_line_km_km2')


def finite(value, name):
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Invalid numeric value for '+name) from exc
    if not math.isfinite(n):
        raise ValueError('Non-finite numeric value: '+name)
    return n


def sha256_file(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda: f.read(1<<20), b''):
            h.update(block)
    return h.hexdigest()


def weighted_pearson(points):
    """Return area-weighted descriptive Pearson r or None; pairs=(x,y,area)."""
    pts=[]
    for x,y,w in points:
        x,y,w=finite(x,'x'),finite(y,'y'),finite(w,'area')
        if w<=0: raise ValueError('Area weights must be positive')
        pts.append((x,y,w))
    if len(pts)<3:return None
    sw=math.fsum(p[2] for p in pts)
    mx=math.fsum(x*w for x,y,w in pts)/sw
    my=math.fsum(y*w for x,y,w in pts)/sw
    xx=math.fsum(w*(x-mx)**2 for x,y,w in pts)
    yy=math.fsum(w*(y-my)**2 for x,y,w in pts)
    if xx<=1e-16 or yy<=1e-16:return None
    r=math.fsum(w*(x-mx)*(y-my) for x,y,w in pts)/math.sqrt(xx*yy)
    return min(1.0,max(-1.0,r))


def surface_ratios(surf_ground, surf_roof, surf_wall, plan_area):
    area=finite(plan_area,'region area')
    if area<=0:raise ValueError('Region area must be positive')
    vals=[finite(v,'3D surface area') for v in (surf_ground,surf_roof,surf_wall)]
    if any(v<0 for v in vals):raise ValueError('Negative 3D surface area')
    return tuple(v/area for v in vals)


def verified_source(path):
    """Load reference manifest and check CSV checksum; QGIS/GPKG checked later."""
    path=os.path.abspath(path)
    base=os.path.dirname(path)
    with open(path,encoding='utf-8') as f:meta=json.load(f)
    if meta.get('schema')!=REGIONAL_SCHEMA:
        raise ValueError('Select Regional_HIP_*/regional_hip_manifest.json (v0.1)')
    csv_path=os.path.join(base,'regional_hip.csv')
    gpkg_path=os.path.join(base,'regional_hip_provisional.gpkg')
    if any(not os.path.isfile(p) for p in (csv_path,gpkg_path)):
        raise ValueError('Regional HIP CSV/GPKG not found beside manifest')
    if sha256_file(csv_path)!=meta.get('csv_sha256'):
        raise ValueError('Regional HIP CSV SHA-256 differs from source manifest')
    n=int(meta.get('zone_count',0))
    if n<=0 or n>MAX_REGIONS:raise ValueError('Invalid region count')
    with open(csv_path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        need=['region_id','region_name','area_m2','hip_c','ground_c','roof_c','wall_c',
              'cover_pct','hole_pct','surf_ground_m2','surf_roof_m2','surf_wall_m2','quality']
        if any(k not in (reader.fieldnames or []) for k in need):
            raise ValueError('Regional HIP CSV lacks required source columns')
        source_rows={}
        for row in reader:
            rid=int(row['region_id'])
            if rid in source_rows or rid<=0:raise ValueError('Duplicate regional HIP ID')
            area=finite(row['area_m2'],'region area')
            if area<=0:raise ValueError('Invalid regional HIP area')
            temp=finite(row['hip_c'],'HIP')
            parts=[finite(row[k],k) for k in ('ground_c','roof_c','wall_c')]
            if abs(math.fsum(parts)-temp)>2e-5:
                raise ValueError('Regional HIP contribution sum mismatch in region '+str(rid))
            surface_ratios(row['surf_ground_m2'],row['surf_roof_m2'],row['surf_wall_m2'],area)
            if row['cover_pct']:
                cov=finite(row['cover_pct'],'cover_pct')
                if not 0<=cov<=100:raise ValueError('Invalid cover_pct')
            source_rows[rid]=row
    if len(source_rows)!=n:raise ValueError('Regional HIP CSV count mismatch')
    if not meta.get('crs') or not meta.get('timestamp_jst') or not meta.get('patch_generation_token'):
        raise ValueError('Regional HIP source provenance incomplete')
    return meta,source_rows,gpkg_path,path


def available_layers(project, skip_id):
    from qgis.core import QgsVectorLayer,QgsWkbTypes
    polygons=[];lines=[]
    for lyr in project.mapLayers().values():
        if not isinstance(lyr,QgsVectorLayer) or not lyr.isValid() or lyr.id()==skip_id:
            continue
        if not lyr.crs().isValid():continue
        # Exclude numeric/model outputs to avoid accidental landcover classification.
        if lyr.name().lower().startswith(('campus3d surface','campus3d mrt',
                'campus3d regional','campus3d coverage','campus3d hip')):
            continue
        count=lyr.featureCount()
        if not 0<count<=MAX_SOURCE_FEATURES:continue
        gtype=lyr.geometryType()
        label=f'{lyr.name()}  [{count:,} features; {lyr.crs().authid()}]'
        if gtype==QgsWkbTypes.PolygonGeometry:polygons.append((label,lyr))
        elif gtype==QgsWkbTypes.LineGeometry:lines.append((label,lyr))
    polygons.sort(key=lambda t:t[0].lower());lines.sort(key=lambda t:t[0].lower())
    return polygons,lines


def select_explicit_layers(polygons,lines):
    """No inferred landcover class: user selects precise geometry data sources."""
    from qgis.PyQt.QtWidgets import (QDialog,QVBoxLayout,QLabel,QListWidget,
        QDialogButtonBox,QAbstractItemView,QScrollArea,QWidget)
    from qgis.PyQt.QtCore import Qt
    dlg=QDialog()
    dlg.setWindowTitle('Campus3D Regional HIP Context v0.2.1 — data sources')
    dlg.resize(690,740)
    outer=QVBoxLayout(dlg)
    outer.addWidget(QLabel('Select source layers ONLY when their geometry represents the stated category.\n'
        'Leave a category unselected if no suitable layer exists. Ctrl is not required for multi-select.'))
    scroll=QScrollArea();scroll.setWidgetResizable(True)
    body=QWidget();layout=QVBoxLayout(body);selectors={}
    specs=[('building','建物の水平フットプリント（ポリゴン、複数選択可）',polygons),
           ('road','舗装道路の面ポリゴン（道路面積率、任意）',polygons),
           ('green','緑地・植生域の面ポリゴン（緑地率、任意）',polygons),
           ('road_line','道路中心線（道路線密度。道路端線・二重線は選ばない）',lines)]
    for key,title,items in specs:
        layout.addWidget(QLabel(title))
        lst=QListWidget();lst.setSelectionMode(QAbstractItemView.MultiSelection)
        lst.setMinimumHeight(105)
        for label,layer in items:
            from qgis.PyQt.QtWidgets import QListWidgetItem
            itm=QListWidgetItem(label)
            itm.setData(Qt.UserRole,layer.id());lst.addItem(itm)
        layout.addWidget(lst);selectors[key]=lst
    layout.addWidget(QLabel('選択しない項目は欠測（NULL）になります。0%とみなしません。'))
    scroll.setWidget(body);outer.addWidget(scroll)
    buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept);buttons.rejected.connect(dlg.reject)
    outer.addWidget(buttons)
    if dlg.exec()!=QDialog.Accepted:return None
    chosen={}
    for key,lst in selectors.items():
        names=[item.data(Qt.UserRole) for item in lst.selectedItems()]
        chosen[key]=names
    as_polygon={lid:key for key,ids in chosen.items() if key!='road_line' for lid in ids}
    if sum(len(ids) for key,ids in chosen.items() if key!='road_line')!=len(as_polygon):
        raise ValueError('Same polygon layer chosen for multiple incompatible land-cover categories')
    return chosen


def source_index(layers,ids):
    from qgis.core import QgsSpatialIndex
    out=[]
    for lid in ids:
        layer=layers.get(lid)
        if layer is None or not layer.isValid():
            raise ValueError('Selected GIS source layer became unavailable')
        out.append((layer,QgsSpatialIndex(layer.getFeatures())))
    return out


def clipped_measure(zone, target_crs, specs, kind, project):
    """Union selected source features per class inside each region to avoid duplicates."""
    from qgis.core import QgsCoordinateTransform,QgsFeatureRequest,QgsGeometry,QgsWkbTypes
    pieces=[]
    for src,index in specs:
        to_src=QgsCoordinateTransform(target_crs,src.crs(),project)
        to_dst=QgsCoordinateTransform(src.crs(),target_crs,project)
        native_extent=to_src.transformBoundingBox(zone.boundingBox())
        ids=index.intersects(native_extent)
        if not ids:continue
        req=QgsFeatureRequest().setFilterFids(list(ids))
        for ft in src.getFeatures(req):
            g=QgsGeometry(ft.geometry())
            if g.isNull() or g.isEmpty():continue
            if not g.isGeosValid():
                raise ValueError('Invalid source geometry in '+src.name()+' feature '+str(ft.id())+'; fix before analysis')
            if src.crs()!=target_crs:g.transform(to_dst)
            if not g.intersects(zone):continue
            clipped=g.intersection(zone)
            if clipped.isNull() or clipped.isEmpty():continue
            if kind=='line':
                if clipped.length()>1e-9:pieces.append(clipped)
            else:
                if clipped.area()>1e-9:pieces.append(clipped)
    if not pieces:return 0.0
    merged=QgsGeometry.unaryUnion(pieces)
    if merged.isNull() or merged.isEmpty():
        raise ValueError('Failed source geometry union')
    return merged.length() if kind=='line' else merged.area()


def output_report(rows,choices,meta,assoc):
    out=['Campus3D Regional HIP Context v0.2.1 — DESCRIPTIVE / PROVISIONAL',
         'Source timestamp: '+str(meta['timestamp_jst']),
         'CRS: '+str(meta['crs']),
         'Regions: '+str(len(rows)),
         'Source HIP remains unchanged. Quality flags have NOT been promoted to validated.',
         'Surface ratios use 3D surface area / region horizontal area; not building coverage.',
         'Road line density is km of vector lines / km2; not paved road surface fraction.',
         'Selected GIS polygon categories have no automatic classification or semantic validation.',
         'Within each category intersecting geometries are unioned (no duplicate coverage).',
         'Different categories may overlap; their percentages must not be added to 100%.',
         'Area-weighted Pearson r is descriptive, does not imply causation or independent observations.',
         '']
    quality={}
    for row in rows:
        q=row['quality'];quality[q]=quality.get(q,0)+1
    out.append('Region quality: '+str(quality))
    for key,items in choices.items():
        out.append('Selected '+key+' layers: '+(', '.join(items) if items else '(none; output NULL)'))
    out.extend(['','Associations with HIP (area-weighted Pearson r):'])
    for key,stats in assoc.items():
        def fmt(pair):
            return ('not estimable' if pair['r'] is None else 'r=%.4f'%pair['r'])+f' (n={pair["n"]})'
        out.append('  %s: all=%s; COVER>=98%% subset=%s'%(key,fmt(stats['all']),fmt(stats['high_coverage'])))
    return '\n'.join(out)+'\n'


def correlations(rows):
    results={}
    for measure in MEASURES:
        subset=[]
        high=[]
        for r in rows:
            x=r.get(measure)
            if x is None:continue
            pt=(r['hip_c'],x,r['area_m2'])
            subset.append(pt)
            if r['quality']=='PROVISIONAL' and r['cover_pct'] is not None and r['cover_pct']>=98.0:
                high.append(pt)
        results[measure]={'all':{'n':len(subset),'r':weighted_pearson(subset)},
                          'high_coverage':{'n':len(high),'r':weighted_pearson(high)}}
    return results


def run_regional_context(manifest_path=None):
    from qgis.core import (QgsCoordinateReferenceSystem,QgsVectorLayer,QgsWkbTypes,
        QgsGeometry,QgsFeature,QgsField,QgsVectorFileWriter,QgsProject,Qgis)
    from qgis.PyQt.QtCore import QVariant,Qt,QCoreApplication
    from qgis.PyQt.QtWidgets import QFileDialog,QProgressDialog,QMessageBox

    if manifest_path is None:
        manifest_path,_=QFileDialog.getOpenFileName(None,'Select Regional_HIP_*/regional_hip_manifest.json','','JSON (*.json)')
    if not manifest_path:return
    meta,source_rows,gpkg_path,source_manifest=verified_source(manifest_path)
    project=QgsProject.instance()
    crs=QgsCoordinateReferenceSystem(meta['crs'])
    if not crs.isValid() or crs.isGeographic() or crs.mapUnits()!=Qgis.DistanceUnit.Meters:
        raise ValueError('Projected metre CRS required')
    src=QgsVectorLayer(gpkg_path+'|layername=regional_hip','Verified Regional HIP input','ogr')
    if not src.isValid() or src.geometryType()!=QgsWkbTypes.PolygonGeometry or src.crs()!=crs:
        raise ValueError('Regional HIP GPKG invalid or CRS mismatch')
    if src.featureCount()!=len(source_rows):
        raise ValueError('Regional HIP GPKG region count differs from CSV')
    all_features={}
    for f in src.getFeatures():
        rid=int(f['region_id'])
        if rid not in source_rows or rid in all_features:
            raise ValueError('GPKG contains missing or duplicate region ID')
        row=source_rows[rid]
        for k in ('area_m2','hip_c','ground_c','roof_c','wall_c','surf_ground_m2','surf_roof_m2','surf_wall_m2'):
            a=finite(row[k],k);b=finite(f[k],k)
            if abs(a-b)>max(1e-5,abs(a)*1e-7):
                raise ValueError('Regional HIP GPKG differs from CSV: '+k+' region '+str(rid))
        if f['quality']!=row['quality']:
            raise ValueError('Regional HIP quality differs in GPKG')
        geom=QgsGeometry(f.geometry())
        if geom.isNull() or geom.isEmpty() or not geom.isGeosValid():
            raise ValueError('Region geometry missing/invalid: '+str(rid))
        if abs(geom.area()-finite(row['area_m2'],'region area'))>max(.02,geom.area()*1e-7):
            raise ValueError('Regional HIP area differs from geometry')
        all_features[rid]=(f,geom)
    if set(all_features)!=set(source_rows):raise ValueError('GPKG missing region IDs')

    polygons,lines=available_layers(project,src.id())
    choice=select_explicit_layers(polygons,lines)
    if choice is None:return
    layer_map={lyr.id():lyr for label,lyr in polygons+lines}
    cat_index={k:source_index(layer_map,choice[k]) for k in choice}
    spec_names={k:[layer_map[lid].name()+' ('+layer_map[lid].crs().authid()+')' for lid in v] for k,v in choice.items()}
    rows=[]
    prog=QProgressDialog('Campus3D Regional HIP Context: geometry overlays ...','Cancel',0,len(all_features),None)
    prog.setWindowModality(Qt.WindowModal);prog.setMinimumDuration(1000)
    try:
        for ix,rid in enumerate(sorted(all_features),1):
            original,zone=all_features[rid]
            srcrow=source_rows[rid]
            area=finite(srcrow['area_m2'],'region area')
            ground,roof,wall=surface_ratios(srcrow['surf_ground_m2'],srcrow['surf_roof_m2'],srcrow['surf_wall_m2'],area)
            row={k:srcrow[k] for k in srcrow}
            row.update(region_id=rid, area_m2=area,hip_c=finite(srcrow['hip_c'],'hip_c'),
                       roof_surf_ratio=roof,wall_surf_ratio=wall,ground_surf_ratio=ground)
            row['cover_pct']=finite(srcrow['cover_pct'],'cover_pct') if srcrow['cover_pct'] else None
            for key,field in [('building','building_cover_pct'),('road','road_cover_pct'),('green','green_cover_pct')]:
                row[field]=None
                if choice[key]:
                    measured=clipped_measure(zone,crs,cat_index[key],'polygon',project)
                    if measured>area+max(.01,area*1e-6):
                        raise ValueError('Selected polygon coverage exceeds region area; verify source layers')
                    row[field]=100.0*min(measured,area)/area
            row['road_line_km_km2']=None
            if choice['road_line']:
                measured=clipped_measure(zone,crs,cat_index['road_line'],'line',project)
                row['road_line_km_km2']=measured*1000.0/area
            rows.append(row)
            prog.setValue(ix)
            if ix%5==0:QCoreApplication.processEvents()
            if prog.wasCanceled():raise RuntimeError('Canceled: no output was written')
    finally:
        prog.close()
    assoc=correlations(rows)
    base=os.path.dirname(source_manifest)
    out_folder=os.path.join(base,'Regional_Context_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    if os.path.exists(out_folder):raise RuntimeError('Output folder already exists')
    os.makedirs(out_folder)
    output_csv=os.path.join(out_folder,'regional_hip_context.csv')
    output_gpkg=os.path.join(out_folder,'regional_hip_context.gpkg')
    output_json=os.path.join(out_folder,'context_manifest.json')
    output_report_path=os.path.join(out_folder,'context_report.txt')
    newfields=('roof_surf_ratio','wall_surf_ratio','ground_surf_ratio',
               'building_cover_pct','road_cover_pct','green_cover_pct','road_line_km_km2')
    try:
        csv_fields=list(source_rows[next(iter(source_rows))].keys())+list(newfields)
        with open(output_csv,'w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=csv_fields,lineterminator='\n')
            w.writeheader()
            for row in rows:w.writerow({k:row.get(k) for k in csv_fields})
        out=QgsVectorLayer('MultiPolygon?crs='+meta['crs'],'Campus3D Regional HIP Context','memory')
        # GPKG auto-creates FID. Keep the actual scientific source fields, not its provider FID.
        fields=[QgsField(f) for f in src.fields() if f.name().lower()!='fid']
        fields.extend(QgsField(k,QVariant.Double) for k in newfields)
        out.dataProvider().addAttributes(fields);out.updateFields()
        feats=[]
        for row in rows:
            r_id=row['region_id'];sfeat,sgeom=all_features[r_id]
            geom=QgsGeometry(sgeom);geom.convertToMultiType()
            f=QgsFeature(out.fields());f.setGeometry(geom)
            f.setAttributes([sfeat[k] for k in out.fields().names() if k not in newfields]+[row[k] for k in newfields])
            feats.append(f)
        if not out.dataProvider().addFeatures(feats)[0]:
            raise RuntimeError('Cannot construct context output layer')
        out.updateExtents()
        opts=QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName='GPKG';opts.layerName='regional_hip_context';opts.fileEncoding='UTF-8'
        status=QgsVectorFileWriter.writeAsVectorFormatV3(out,output_gpkg,project.transformContext(),opts)
        if status[0]!=QgsVectorFileWriter.NoError:raise RuntimeError('Could not write GPKG: '+str(status))
        loaded=QgsVectorLayer(output_gpkg+'|layername=regional_hip_context',
            'Campus3D Regional HIP Context '+str(meta['timestamp_jst']),'ogr')
        if not loaded.isValid() or loaded.featureCount()!=len(rows):
            raise RuntimeError('Exported region count mismatch')
        loaded.setRenderer(src.renderer().clone())
        # Store a single layer in a dedicated group to avoid clutter.
        group=project.layerTreeRoot().findGroup('Campus3D | Regional HIP Analysis')
        if group is None:group=project.layerTreeRoot().insertGroup(0,'Campus3D | Regional HIP Analysis')
        project.addMapLayer(loaded,False)
        node=group.addLayer(loaded)
        node.setExpanded(False)
        with open(output_report_path,'w',encoding='utf-8') as f:f.write(output_report_fn(rows,spec_names,meta,assoc))
        with open(output_json,'w',encoding='utf-8') as f:
            json.dump({'schema':CONTEXT_SCHEMA,'source_manifest':source_manifest,
                'source_csv_sha256':meta['csv_sha256'],'time_jst':meta['timestamp_jst'],
                'crs':meta['crs'],'patch_generation_token':meta['patch_generation_token'],
                'region_count':len(rows),'region_quality_counts':dict((q,sum(r['quality']==q for r in rows)) for q in set(r['quality'] for r in rows)),
                'selected_layers':spec_names,'extra_fields':list(newfields),
                'correlation':assoc,'csv_sha256':sha256_file(output_csv),
                'notes':['Roof/wall/ground ratios are 3D surface area / plan area, not building footprint fractions',
                         'Polygon coverage percentages require explicit source semantic selection',
                         'ROAD line density is km/km2; not a road area fraction',
                         'Correlation is descriptive, spatial observations are dependent',
                         'Source quality status remains provisional and gaps are not imputed']},
                f,ensure_ascii=False,indent=2,allow_nan=False)
    except Exception:
        print('Partial new output folder if any:',out_folder)
        raise
    print('Campus3D Regional Context: OK,',len(rows),'regions,',meta['crs'])
    print(output_report_fn(rows,spec_names,meta,assoc))
    print('Output:',out_folder)
    QMessageBox.information(None,'Campus3D Regional HIP Context',
       f'Analysis completed: {len(rows)} regions.\nSource quality retained.\n\n{out_folder}')


def output_report_fn(rows,choices,meta,assoc):
    return output_report(rows,choices,meta,assoc)


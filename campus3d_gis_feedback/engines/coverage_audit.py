# -*- coding: utf-8 -*-
r"""Campus3D GIS Surface Coverage Audit v0.1 (QGIS 3.44 Python Console).

Read-only diagnosis of existing Post-Sim v0.4.1/0.4.2 output. No editing of
source GIS layers, source GeoPackages, EnvSim NPZ, or HIP values. Creates its
own audit folder with a 2 m raster mask and reports. Optional click checks
use exact feature geometries (not the raster approximation).

Run: exec(open(r'C:\path\qgis_surface_coverage_audit.py', encoding='utf-8').read())
Choose the SURFACE_tNNN/gis_manifest.json (not HIP_tNNN). Click white areas in
QGIS *2D* canvas after the scan. Re-select the Pan tool to stop inspecting.
"""

import csv
import glob
import hashlib
import json
import math
import os
from pathlib import Path
from datetime import datetime

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsPalettedRasterRenderer, QgsPointXY,
    QgsGeometry, QgsRectangle, QgsFeatureRequest, Qgis, QgsColorRampShader,
)
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QFileDialog

# At 2 m, a 1114 x 817 m extent needs roughly 230,000 pixels. The raster is
# ONLY a reconnaissance measure of horizontal coverage; do not use it as HIP A_plan.
RASTER_RESOLUTION_M = 2.0
MAX_PIXEL_COUNT = 2_000_000
ROLES = ('GROUND', 'ROOF', 'WALL')


def _finite(value, label):
    try:
        v = float(value)
    except (ValueError, TypeError, OverflowError) as ex:
        raise ValueError('Invalid '+label) from ex
    if not math.isfinite(v):
        raise ValueError('Non-finite '+label)
    return v


def _hash(path):
    sha = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            sha.update(b)
    return sha.hexdigest()


def _same_number(a, b, rel=1e-7, abs_tol=1e-5):
    return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=abs_tol)


def _extent(metadata):
    src = metadata.get('source_manifest_extent')
    if not isinstance(src, dict):
        raise ValueError('No source_manifest_extent in Surface manifest')
    out = {k: _finite(src[k], k) for k in ('xmin', 'xmax', 'ymin', 'ymax')}
    if out['xmax'] <= out['xmin'] or out['ymax'] <= out['ymin']:
        raise ValueError('Invalid extent')
    return out


def _validate_surface_export(manifest_path):
    base = os.path.dirname(os.path.abspath(manifest_path))
    with open(manifest_path, 'r', encoding='utf-8') as f:
        m = json.load(f)
    if m.get('schema') != 'Campus3D_GIS_Feedback_Surface_v0.2' or m.get('quantity') != 'SURFACE':
        raise ValueError('Select gis_manifest.json from SURFACE_tNNN, not HIP_tNNN')
    code = m.get('crs', '')
    crs = QgsCoordinateReferenceSystem(code)
    if not crs.isValid() or crs.isGeographic() or crs.mapUnits() != Qgis.DistanceUnit.Meters:
        raise ValueError('Surface export requires a projected metre CRS')
    ext = _extent(m)
    csv_name = m.get('csv_file', 'surface_patches.csv')
    csv_path = os.path.abspath(os.path.join(base, csv_name))
    if os.path.dirname(csv_path) != base or not os.path.isfile(csv_path):
        raise ValueError('Surface CSV absent or outside export folder')
    if _hash(csv_path) != m.get('csv_sha256'):
        raise ValueError('Surface CSV SHA-256 differs from manifest')
    ox = _finite(m['origin']['x'], 'origin.x')
    oy = _finite(m['origin']['y'], 'origin.y')
    seen = set()
    indexed = {}   # exact patch ID -> role, temperature, area, projected XY
    count = dict.fromkeys(ROLES, 0)
    areas = {k: [] for k in ROLES}
    temp_areas = {k: [] for k in ROLES}
    temp_min, temp_max = float('inf'), float('-inf')
    # Full CSV integrity preflight; do NOT invent missing patches from a raster.
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != m.get('field_names'):
            raise ValueError('Surface CSV header differs from manifest')
        for row in reader:
            pid = int(row['patch_id'])
            role = row['kind']
            if pid <= 0 or pid in seen:
                raise ValueError('Non-positive or duplicate patch_id: '+str(pid))
            if role not in ROLES:
                raise ValueError('Unexpected patch kind '+role)
            if row['time_jst'] != m['timestamp_jst']:
                raise ValueError('Timestamp mismatch in Surface CSV')
            seen.add(pid)
            count[role] += 1
            area = _finite(row['area_m2'], 'area_m2')
            ts = _finite(row['surface_c'], 'surface_c')
            cx = _finite(row['x_gis'], 'x_gis')
            cy = _finite(row['y_gis'], 'y_gis')
            if area <= 0:
                raise ValueError('Invalid 3D area for patch '+str(pid))
            indexed[pid] = (role, ts, area, cx, cy)
            if abs((ox + _finite(row['x_local_m'], 'x_local_m')) - cx) > 1e-5 or \
               abs((oy + _finite(row['y_local_m'], 'y_local_m')) - cy) > 1e-5:
                raise ValueError('Origin/centroid mismatch for patch '+str(pid))
            if role in ('GROUND', 'ROOF'):
                x = [_finite(row['x%d_gis' % i], 'vertex x') for i in (1, 2, 3)]
                y = [_finite(row['y%d_gis' % i], 'vertex y') for i in (1, 2, 3)]
                a2d = abs((x[1]-x[0])*(y[2]-y[0]) - (x[2]-x[0])*(y[1]-y[0])) / 2.0
                if a2d <= 1e-9 or a2d > area * 1.021 + 0.015:
                    raise ValueError('Invalid projected geometry or 3D area, patch '+str(pid))
                if abs(sum(x)/3.0-cx) > 0.04 or abs(sum(y)/3.0-cy) > 0.04:
                    raise ValueError('Triangle/centroid mismatch for patch '+str(pid))
            else:
                # Wall corners in the exported CSV are projected but are not a 2D footprint.
                for i in (1, 2, 3):
                    _finite(row['x%d_gis' % i], 'wall vertex x')
                    _finite(row['y%d_gis' % i], 'wall vertex y')
            areas[role].append(area)
            temp_areas[role].append((ts, area))
            temp_min = min(temp_min, ts)
            temp_max = max(temp_max, ts)
    if sum(count.values()) != int(m['patch_count']) or count != m['patch_counts']:
        raise ValueError('Surface CSV counts differ from manifest')
    layers = {}
    ogr_layers = {}
    ogr_datasets = []
    for role in ROLES:
        if count[role] == 0:
            continue
        fn = os.path.join(base, 'surface_'+role.lower()+'.gpkg')
        if not os.path.isfile(fn):
            raise ValueError('GPKG not found. Run qgis_import_surface.py first: '+fn)
        name = 'surface_'+role.lower()
        qlayer = QgsVectorLayer(fn+'|layername='+name, 'Audit source '+name, 'ogr')
        if not qlayer.isValid() or qlayer.featureCount() != count[role]:
            raise ValueError('GeoPackage geometry count mismatch for '+role)
        if qlayer.crs() != crs:
            raise ValueError('GeoPackage CRS mismatch for '+role)
        ds = ogr.Open(fn, 0)
        if ds is None:
            raise ValueError('OGR could not open '+fn)
        ogr_layer = ds.GetLayerByName(name)
        if ogr_layer is None or ogr_layer.GetFeatureCount() != count[role]:
            raise ValueError('OGR layer/count mismatch for '+role)
        layers[role] = qlayer
        ogr_layers[role] = ogr_layer
        ogr_datasets.append(ds)  # keep owner alive during rasterization
    return m, ext, count, layers, ogr_layers, ogr_datasets, areas, temp_areas, (temp_min, temp_max), indexed


def _matched_hip(surface_meta, surface_dir, counts, areas, ts_area, indexed):
    """Optional HIP crosscheck. Skip ambiguity, never silently pair different runs."""
    parent = os.path.dirname(surface_dir)
    found = []
    for path in glob.glob(os.path.join(parent, 'HIP_t*', 'gis_manifest.json')):
        try:
            with open(path, encoding='utf-8') as f:
                m = json.load(f)
            if (m.get('schema') == 'Campus3D_GIS_Feedback_HIP_v0.1'
                and m.get('timestamp_jst') == surface_meta['timestamp_jst']
                and m.get('patch_generation_token') == surface_meta.get('patch_generation_token')
                and m.get('crs') == surface_meta.get('crs')):
                found.append((path, m))
        except (ValueError, OSError, TypeError):
            continue
    if len(found) != 1:
        return ('HIP crosscheck: SKIPPED (%d matching HIP exports; need exactly 1)' % len(found), None)
    hp, hip = found[0]
    base = os.path.dirname(hp)
    if any(not _same_number(_extent(surface_meta)[k], hip['source_manifest_extent'][k],abs_tol=0.001)
           for k in ('xmin','xmax','ymin','ymax')):
        return ('HIP crosscheck: FAILED (different source extent)', None)
    sf = os.path.join(base, hip['summary_file'])
    pf = os.path.join(base, hip['patch_file'])
    if _hash(sf) != hip.get('summary_sha256') or _hash(pf) != hip.get('patch_sha256'):
        return ('HIP crosscheck: FAILED (HIP checksums differ)', None)
    if int(hip.get('patch_count', -1)) != sum(counts.values()):
        return ('HIP crosscheck: FAILED (different patch count)', None)
    # Confirm the same patch IDs, classifications, temperature, 3D area and XY —
    # independent of the human-readable HIP summary row aggregation.
    checked = set()
    with open(pf, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != hip.get('patch_fields'):
            return ('HIP crosscheck: FAILED (HIP patch CSV columns changed)', None)
        for row in reader:
            pid = int(row['patch_id'])
            if pid not in indexed or pid in checked:
                return ('HIP crosscheck: FAILED (missing / duplicate patch_id in HIP)', None)
            checked.add(pid)
            role, temperature, area3d, x, y = indexed[pid]
            if (row['kind'] != role or row['timestamp_jst'] != surface_meta['timestamp_jst']
                or not _same_number(row['surface_c'], temperature, abs_tol=1e-5)
                or not _same_number(row['area_m2'], area3d, abs_tol=1e-4)
                or not _same_number(row['x_gis'], x, abs_tol=0.01)
                or not _same_number(row['y_gis'], y, abs_tol=0.01)):
                return ('HIP crosscheck: FAILED (per-patch role / value / area / XY)', None)
    if len(checked) != len(indexed):
        return ('HIP crosscheck: FAILED (HIP patch set incomplete)', None)
    with open(sf, encoding='utf-8-sig', newline='') as f:
        summaries = {r['role']: r for r in csv.DictReader(f)}
    if set(summaries) != {'ALL', 'GROUND', 'ROOF', 'WALL'}:
        return ('HIP crosscheck: FAILED (invalid HIP role summary)', None)
    ta = _finite(hip['air_c'], 'HIP air_c')
    plan = _finite(hip['plan_area_m2'], 'HIP plan_area_m2')
    actual = {}
    for role in ROLES:
        true_area = math.fsum(areas[role])
        contribution = math.fsum(a*(t-ta) for t, a in ts_area[role])/plan
        saved = summaries[role]
        if (int(saved['patch_count']) != counts[role]
            or not _same_number(true_area, saved['surface_area_m2'],abs_tol=0.02)
            or not _same_number(contribution, saved['hip_contribution_c'],abs_tol=5e-5)):
            return ('HIP crosscheck: FAILED ('+role+' count / area / contribution mismatch)', None)
        actual[role] = contribution
    if not _same_number(math.fsum(actual.values()), summaries['ALL']['hip_contribution_c'],abs_tol=5e-5):
        return ('HIP crosscheck: FAILED (sum of HIP contributions)', None)
    return ('HIP crosscheck: PASS (same timestamp, patch generation, area and contribution)',
            {'hip_c': float(summaries['ALL']['hip_contribution_c']),
             'air_c': ta, 'plan_area_m2': plan, 'path': hp})


def _rasterize(ogr_layer, extent, res, dims, gdal_srs):
    width, height = dims
    mem = gdal.GetDriverByName('MEM').Create('', width, height, 1, gdal.GDT_Byte)
    if mem is None:
        raise RuntimeError('GDAL MEM raster creation failed')
    mem.SetGeoTransform((extent['xmin'],res,0,extent['ymax'],0,-res))
    mem.SetProjection(gdal_srs.ExportToWkt())
    mem.GetRasterBand(1).Fill(0)
    ret = gdal.RasterizeLayer(mem, [1], ogr_layer, burn_values=[1])
    if ret != 0:
        raise RuntimeError('GDAL RasterizeLayer failed (code %d)' % ret)
    data = mem.GetRasterBand(1).ReadAsArray()
    if data is None:
        raise RuntimeError('GDAL raster read failed')
    return (data > 0)


def _save_raster(path, classifications, extent, res, srs):
    h, w = classifications.shape
    ds = gdal.GetDriverByName('GTiff').Create(
        path, w, h, 1, gdal.GDT_Byte,
        options=['COMPRESS=LZW','TILED=YES','BIGTIFF=IF_SAFER'])
    if ds is None:
        raise RuntimeError('Cannot create audit raster '+path)
    ds.SetGeoTransform((extent['xmin'], res, 0.0, extent['ymax'], 0.0, -res))
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(classifications)
    band.SetNoDataValue(255)
    band.FlushCache()
    ds.FlushCache()
    ds = None


def _add_overlay(raster_path):
    layer = QgsRasterLayer(raster_path, 'Campus3D COVERAGE AUDIT: MAGENTA = no ground/roof')
    if not layer.isValid():
        raise RuntimeError('Failed to load audit raster')
    # Convert documented QGIS color-ramp items into palette class records.
    # This avoids relying on version-specific Python constructors for Class.
    items = [
        QgsColorRampShader.ColorRampItem(0, QColor(255, 0, 185, 220), 'NO GROUND/ROOF'),
        QgsColorRampShader.ColorRampItem(1, QColor(45, 185, 82, 0), 'GROUND only'),
        QgsColorRampShader.ColorRampItem(2, QColor(30, 160, 250, 0), 'ROOF only'),
        QgsColorRampShader.ColorRampItem(3, QColor(180, 75, 225, 0), 'GROUND+ROOF'),
    ]
    cats = QgsPalettedRasterRenderer.colorTableToClassData(items)
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, cats))
    QgsProject.instance().addMapLayer(layer)
    layer.triggerRepaint()
    return layer


class _ClickCheck:
    """Precise point-in-triangle test, independent of raster cell resolution."""

    def __init__(self, layers, crs, destination):
        self.layers = layers
        self.crs = crs
        self.destination = destination
        self.canvas = iface.mapCanvas()
        self.tool = QgsMapToolEmitPoint(self.canvas)
        self.tool.canvasClicked.connect(self.clicked)
        self.log_path = os.path.join(destination, 'clicked_spots.csv')
        with open(self.log_path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(('click_id','x_gis','y_gis','ground_patch_ids','roof_patch_ids',
                        'ground_temp_c','roof_temp_c','classification'))
        self.n = 0

    def start(self):
        self.canvas.setMapTool(self.tool)
        print('CLICK TEST STARTED: Click white gap(s) in the QGIS **2D** canvas.')
        print('Also click one normal colored surface. Then click Pan (hand icon) to leave inspection mode.')
        print('Exact point tests are saved to:', self.log_path)

    def clicked(self, point_canvas, button):
        try:
            canvas_crs = self.canvas.mapSettings().destinationCrs()
            point = QgsCoordinateTransform(canvas_crs, self.crs, QgsProject.instance()).transform(
                QgsPointXY(point_canvas.x(), point_canvas.y()))
            geom_point = QgsGeometry.fromPointXY(point)
            result = {}
            for role in ('GROUND','ROOF'):
                matches = []
                layer = self.layers.get(role)
                if layer is not None:
                    eps = 0.0001
                    rect = QgsRectangle(point.x()-eps,point.y()-eps,
                                        point.x()+eps,point.y()+eps)
                    for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                        if feature.geometry().intersects(geom_point):
                            matches.append((int(feature['patch_id']),float(feature['surface_c'])))
                result[role] = matches
            if result['GROUND'] and result['ROOF']:
                classification = 'BOTH'
            elif result['GROUND']:
                classification = 'GROUND_ONLY'
            elif result['ROOF']:
                classification = 'ROOF_ONLY'
            else:
                classification = 'NO_GROUND_OR_ROOF'
            self.n += 1
            with open(self.log_path, 'a', encoding='utf-8', newline='') as f:
                csv.writer(f).writerow((self.n, point.x(), point.y(),
                   '|'.join(str(v[0]) for v in result['GROUND'][:12]),
                   '|'.join(str(v[0]) for v in result['ROOF'][:12]),
                   '|'.join(str(round(v[1],4)) for v in result['GROUND'][:12]),
                   '|'.join(str(round(v[1],4)) for v in result['ROOF'][:12]),
                   classification))
            print('AUDIT CLICK %d: (%.3f, %.3f) -> %s; GROUND IDs=%s; ROOF IDs=%s' % (
                self.n, point.x(),point.y(),classification,
                result['GROUND'][:12],result['ROOF'][:12]))
        except Exception as exc:
            print('Campus3D AUDIT CLICK ERROR:',exc)


def run_campus3d_coverage_audit(path=None):
    if path is None:
        path, _ = QFileDialog.getOpenFileName(None,
            'Choose SURFACE_tNNN / gis_manifest.json (not HIP)', '', 'JSON (*.json)')
    if not path:
        print('Campus3D coverage audit canceled')
        return
    print('Validating surface CSV and GPKG ...')
    meta, extent, counts, layers, ogr_layers, ogr_datasets, areas, temp_areas, temp_range, indexed = _validate_surface_export(path)
    base = os.path.dirname(os.path.abspath(path))
    res = RASTER_RESOLUTION_M
    width = math.ceil((extent['xmax']-extent['xmin'])/res)
    height = math.ceil((extent['ymax']-extent['ymin'])/res)
    if width*height > MAX_PIXEL_COUNT:
        raise ValueError('Raster would have too many pixels. Increase RASTER_RESOLUTION_M.')
    srs = osr.SpatialReference()
    if srs.SetFromUserInput(meta['crs']) != 0:
        raise ValueError('GDAL cannot read CRS '+meta['crs'])
    print('Rasterizing projected GROUND and ROOF footprints at %.1f m ...'%res)
    zero = np.zeros((height, width), dtype=bool)
    ground = _rasterize(ogr_layers['GROUND'], extent, res, (width,height), srs) if 'GROUND' in ogr_layers else zero
    roof = _rasterize(ogr_layers['ROOF'], extent, res, (width,height), srs) if 'ROOF' in ogr_layers else zero
    # Exclude the partial extra cells at the top/right edge of the rectangle.
    ycenter = extent['ymax']-(np.arange(height)+0.5)*res
    xcenter = extent['xmin']+(np.arange(width)+0.5)*res
    inside = ((ycenter[:,None] >= extent['ymin']) &
              (xcenter[None,:] <= extent['xmax']))
    classif = ground.astype(np.uint8) + 2*roof.astype(np.uint8)
    classif[~inside] = 255
    counts_px = {k:int(np.count_nonzero((classif==k)&inside)) for k in range(4)}
    valid = int(np.count_nonzero(inside))
    if valid == 0:
        raise ValueError('No coverage raster pixels inside source region')
    try:
        hip_msg, hip_info = _matched_hip(meta,base,counts,areas,temp_areas,indexed)
    except (OSError, ValueError, KeyError, TypeError) as err:
        hip_msg,hip_info = 'HIP crosscheck: FAILED ('+str(err)+')',None
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    target = os.path.join(base,'Coverage_Audit_'+stamp)
    suffix = 2
    while os.path.exists(target):
        target = os.path.join(base,'Coverage_Audit_'+stamp+'_%d'%suffix)
        suffix += 1
    os.makedirs(target)
    raster_path = os.path.join(target, 'coverage_mask_2m.tif')
    _save_raster(raster_path,classif,extent,res,srs)
    report = []
    report.append('Campus3D Surface Coverage Audit v0.1 — QGIS 3.44')
    report.append('Surface manifest: '+path)
    report.append('CRS: '+meta['crs']+'; time: '+meta['timestamp_jst'])
    report.append('Patch-generation token: '+str(meta.get('patch_generation_token')))
    report.append('CSV SHA-256, unique patch IDs, centroids and counts: PASS')
    report.append('Imported GPKG geometry feature counts and CRS: PASS')
    report.append('Surface patches: %d (GROUND %d / ROOF %d / WALL %d)' % (
        sum(counts.values()),counts['GROUND'],counts['ROOF'],counts['WALL']))
    report.append('Surface T range (CSV): %.6f .. %.6f C'%temp_range)
    for role in ROLES:
        report.append('3D area %s: %.3f m2'%(role,math.fsum(areas[role])))
    plan = (extent['xmax']-extent['xmin'])*(extent['ymax']-extent['ymin'])
    report.append('Original extent rectangle: %.4f m2'%plan)
    report.append('Approximate raster coverage at %.2f m pixel (not a union-area proof):'%res)
    labels = {0:'NO GROUND/ROOF',1:'GROUND only',2:'ROOF only',3:'GROUND+ROOF'}
    for k in range(4):
        report.append('  %s: %d cells; approx %.2f m2; %.2f%%'%(labels[k],counts_px[k],
                        counts_px[k]*res**2, 100*counts_px[k]/valid))
    report.append('Note: raster area uses cell centers; boundary pixels and thin triangles are approximate.')
    report.append('Note: holes in the rectangle do NOT automatically imply missing simulation patches; source coverage may intentionally omit areas.')
    report.append('Note: overlapping ground/roof 2D footprints do not invalidate their separate 3D thermal areas.')
    report.append('Note: white visible in 3D view despite existing patches suggests rendering/other-layer masking/occlusion, not necessarily a missing simulation patch.')
    report.append(hip_msg)
    if hip_info:
        report.append('Crosschecked provisional HIP: %.6f C (Ta %.3f C, Aplan %.2f m2)' % (
            hip_info['hip_c'],hip_info['air_c'],hip_info['plan_area_m2']))
    report.append('Source GeoPackages and source HIP results were NOT modified.')
    report_path = os.path.join(target, 'coverage_audit_report.txt')
    with open(report_path,'w',encoding='utf-8') as f:
        f.write('\n'.join(report)+'\n')
    with open(os.path.join(target,'coverage_audit.json'),'w',encoding='utf-8') as f:
        json.dump({'schema':'Campus3D_Coverage_Audit_v0.1',
                   'surface_manifest':path,'timestamp_jst':meta['timestamp_jst'],
                   'crs':meta['crs'],'source_extent':extent,'pixel_size_m':res,
                   'pixel_counts':{str(k):v for k,v in counts_px.items()},
                   'surface_counts':counts,'hip_check':hip_msg,
                   'hip_summary':hip_info,'limitations':['raster approximate',
                   'surface source completeness not established by raster',
                   'Blender source geometry itself not rechecked here']},
                  f,ensure_ascii=False,indent=2)
    overlay_error = None
    try:
        layer = _add_overlay(raster_path)
        iface.setActiveLayer(layer)
    except Exception as exc:
        overlay_error = str(exc)
        print('WARNING: raster saved, but automatic palette rendering failed:',exc)
    checker = _ClickCheck(layers, QgsCoordinateReferenceSystem(meta['crs']), target)
    checker.start()
    # retain QGIS tool + source layer objects for click handling after exec() returns
    globals()['_campus3d_coverage_check'] = checker
    globals()['_campus3d_coverage_layers'] = layers
    print('\n'.join(report))
    print('Saved report:',report_path)
    print('Saved mask:',raster_path)
    print('Click results:',checker.log_path)
    if overlay_error:
        print('If coverage is not visible, open coverage_mask_2m.tif manually.')



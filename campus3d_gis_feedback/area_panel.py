# -*- coding: utf-8 -*-
"""Area drawing widget and lifecycle. Separate from existing route/point UI."""
import csv
import os
import traceback
from datetime import datetime
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,
    QPushButton,QDoubleSpinBox,QPlainTextEdit,QFileDialog,QMessageBox,QApplication)
from qgis.core import QgsProject,QgsPointXY,QgsWkbTypes
from qgis.gui import QgsMapToolEmitPoint,QgsRubberBand
from .area_evaluation import evaluate_area


class AreaPanel:
    def __init__(self,handler):
        self.handler=handler; self.dock=None; self.tool=None; self.prior=None
        self.band=None; self.vertices=[]; self.result=None; self.datasets=[]
        self.crs=None

    def build(self,dock,layout):
        self.dock=dock
        group=QGroupBox('Phase 3｜面評価：区域の熱環境（BETA）')
        lay=QVBoxLayout(group)
        intro=QLabel('区域の角を順にクリックし、3点以上で評価します。MRTは区域内の点、GROUND/ROOFは交差面積で集計します。')
        intro.setWordWrap(True); lay.addWidget(intro)
        lay.addWidget(QLabel('解析データ（日時・解析親フォルダ）'))
        self.target=QComboBox(); self.target.setMinimumContentsLength(12); self.target.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        lay.addWidget(self.target)
        row=QHBoxLayout(); row.addWidget(QLabel('高温判定（以上）'))
        self.threshold=QDoubleSpinBox(); self.threshold.setRange(-50,100); self.threshold.setDecimals(1); self.threshold.setValue(40); self.threshold.setSuffix(' °C'); row.addWidget(self.threshold); lay.addLayout(row)
        row=QHBoxLayout(); self.start_btn=QPushButton('区域作図を開始'); self.start_btn.clicked.connect(self.start); row.addWidget(self.start_btn)
        self.undo_btn=QPushButton('1点戻す'); self.undo_btn.clicked.connect(self.undo); row.addWidget(self.undo_btn); lay.addLayout(row)
        row=QHBoxLayout(); self.finish_btn=QPushButton('区域を確定して評価'); self.finish_btn.clicked.connect(self.finish); row.addWidget(self.finish_btn)
        self.cancel_btn=QPushButton('作図中止'); self.cancel_btn.clicked.connect(self.cancel); row.addWidget(self.cancel_btn); lay.addLayout(row)
        self.status=QLabel('区域を作図してください。'); self.status.setWordWrap(True); lay.addWidget(self.status)
        self.text=QPlainTextEdit(); self.text.setReadOnly(True); self.text.setMinimumHeight(240); lay.addWidget(self.text)
        self.save_btn=QPushButton('面評価結果をCSV保存…'); self.save_btn.clicked.connect(self.save); lay.addWidget(self.save_btn)
        note=QLabel('MRTの高温割合は点数比、Surfaceは投影面積比。WALL・HIPは対象外。レイヤの表示ON/OFFは集計に影響しません。')
        note.setWordWrap(True); lay.addWidget(note); layout.addWidget(group)
        self.refresh()

    def refresh(self):
        if self.dock is None: return
        key=self.target.currentData()
        self.datasets=list(self.dock._point_sets)
        self.target.blockSignals(True); self.target.clear(); selected=0
        for i,(k,stamp,entries,ambiguous) in enumerate(self.datasets):
            kinds='+'.join('MRT' if e[0]=='mrt' else 'Surface' for e in entries)
            folder=k[1][0]; label=f'{stamp} [{kinds}] {folder[-42:]}'
            if ambiguous: label+='【独立実行】'
            self.target.addItem(label,k); self.target.setItemData(i,folder,Qt.ToolTipRole)
            if k==key: selected=i
        if self.datasets: self.target.setCurrentIndex(selected)
        self.target.blockSignals(False); self.sync()
        if not self.datasets: self.status.setText('MRTまたはSurfaceを読み込んだQGZを開いてください。')

    def sync(self):
        if self.dock is None:return
        editing=self.tool is not None
        self.start_btn.setEnabled(bool(self.datasets) and not editing)
        self.target.setEnabled(not editing); self.threshold.setEnabled(not editing)
        self.undo_btn.setEnabled(editing and bool(self.vertices))
        self.finish_btn.setEnabled(editing and len(self.vertices)>=3)
        self.cancel_btn.setEnabled(editing)
        self.save_btn.setEnabled(self.result is not None and not editing)

    def remove_band(self):
        if self.band is not None:
            try: self.handler.iface.mapCanvas().scene().removeItem(self.band)
            except RuntimeError: pass
            self.band=None

    def invalidate(self):
        self.cancel(); self.remove_band(); self.result=None; self.vertices=[]
        if self.dock is not None:
            self.text.clear(); self.status.setText('解析レイヤが変更されました。区域を作図してください。'); self.sync()

    def start(self,*args):
        if self.tool is not None or not self.datasets or self.target.currentIndex()<0:return
        self.handler.comparison.spatial.cancel()
        self.handler.stop_point_evaluation(); self.handler.cancel_route_evaluation()
        canvas=self.handler.iface.mapCanvas(); self.crs=canvas.mapSettings().destinationCrs()
        self.vertices=[]; self.result=None; self.text.clear(); self.remove_band()
        self.band=QgsRubberBand(canvas,QgsWkbTypes.PolygonGeometry)
        self.band.setColor(QColor(26,185,225,45)); self.band.setStrokeColor(QColor('#00bde8')); self.band.setWidth(3)
        self.prior=canvas.mapTool(); self.tool=QgsMapToolEmitPoint(canvas)
        self.tool.canvasClicked.connect(self.clicked); canvas.setMapTool(self.tool)
        self.status.setText('区域の角を順にクリックしてください。3点以上で「区域を確定して評価」。'); self.sync()

    def draw(self):
        if self.band is None:return
        self.band.reset(QgsWkbTypes.PolygonGeometry)
        for x,y in self.vertices:self.band.addPoint(QgsPointXY(x,y),True)

    def clicked(self,p,button):
        if not self.tool or button!=Qt.LeftButton:return
        if self.handler.iface.mapCanvas().mapSettings().destinationCrs()!=self.crs:
            self.cancel(); self.status.setText('座標系が変わりました。区域を描き直してください。'); return
        if len(self.vertices)>=200:self.status.setText('頂点上限200点です。区域を確定してください。'); return
        xy=(float(p.x()),float(p.y()))
        if self.vertices and self.vertices[-1]==xy:return
        self.vertices.append(xy); self.draw(); self.status.setText(f'区域頂点：{len(self.vertices)}点'); self.sync()

    def undo(self,*args):
        if self.tool and self.vertices:
            self.vertices.pop(); self.draw(); self.status.setText(f'区域頂点：{len(self.vertices)}点'); self.sync()

    def release(self):
        if self.tool is None:return
        tool=self.tool; self.tool=None; canvas=self.handler.iface.mapCanvas()
        try:tool.canvasClicked.disconnect(self.clicked)
        except (TypeError,RuntimeError):pass
        if canvas.mapTool() is tool:
            try:
                if self.prior is not None and self.prior is not tool:canvas.setMapTool(self.prior)
                else:self.handler.iface.actionPan().trigger()
            except RuntimeError:self.handler.iface.actionPan().trigger()
        self.prior=None; tool.deleteLater(); self.sync()

    def cancel(self,*args):
        if self.tool is None:return
        self.release(); self.vertices=[]; self.remove_band()
        self.status.setText('区域作図を中止しました。'); self.sync()

    def finish(self,*args):
        if not self.tool or len(self.vertices)<3:return
        if self.handler.iface.mapCanvas().mapSettings().destinationCrs()!=self.crs:
            self.cancel(); self.status.setText('座標系が変わりました。区域を描き直してください。'); return
        i=self.target.currentIndex()
        if not 0<=i<len(self.datasets):return
        self.status.setText('区域内の評価点・パッチ交差面積を計算中…'); self.status.repaint()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.result=evaluate_area(self.vertices,self.crs,self.datasets[i][2],self.threshold.value())
            self.text.setPlainText(self.result['report']); self.release()
            self.status.setText('面評価完了。結果を確認し、必要ならCSV保存してください。')
        except Exception as exc:
            self.status.setText('面評価エラー：'+str(exc)); self.dock.write('Phase 3面評価 ERROR: '+traceback.format_exc())
        finally:
            QApplication.restoreOverrideCursor(); self.sync()

    def save(self,*args):
        if self.result is None:return
        r=self.result; d=os.path.dirname(QgsProject.instance().fileName() or '') or os.path.expanduser('~')
        name='Campus3D_Area_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv'
        path,_=QFileDialog.getSaveFileName(self.dock,'Campus3D — 面評価CSV',os.path.join(d,name),'CSV (*.csv)')
        if not path:return
        if not path.lower().endswith('.csv'):path+='.csv'
        cols=['time_jst','crs','region_wkt','region_area_m2','threshold_c','role','source','matched_count','valid_count','missing_count','mean_c','min_c','max_c','population_sd_c','weight_unit','valid_weight','hot_weight','hot_percent','valid_union_m2','same_role_overlap_m2','combined_valid_surface_m2','combined_uncovered_m2','combined_roles']
        try:
            with open(path,'w',encoding='utf-8-sig',newline='') as f:
                writer=csv.writer(f);writer.writerow(cols)
                for role,s in r['results'].items():
                    writer.writerow([r['stamp'],r['crs'],r['wkt'],r['area'],r['threshold'],role,r['sources'][role],s['matched_count'],s['count'],s['missing_count'],s['mean'],s['minimum'],s['maximum'],s['sd'],'points' if role=='MRT' else 'plan_m2',s['weight'],s['hot_weight'],s['hot_percent'],s.get('union_area'),s.get('overlap_area'),r['covered'],None if r['covered'] is None else r['area']-r['covered'],'+'.join(k for k in ('GROUND','ROOF') if k in r['results'])])
            self.status.setText('CSV保存：'+path)
        except Exception as exc: QMessageBox.warning(self.dock,'Campus3D','CSV保存エラー：'+str(exc))

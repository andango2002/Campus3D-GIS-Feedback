# -*- coding: utf-8 -*-
"""Phase 4.2 in-place point/route/area A/B draw, compare and export."""
import csv
import json
import os
import traceback
from datetime import datetime
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,
    QPushButton,QDoubleSpinBox,QPlainTextEdit,QFileDialog,QMessageBox,QApplication)
from qgis.core import QgsProject,QgsPointXY,QgsWkbTypes
from qgis.gui import QgsMapToolEmitPoint,QgsRubberBand,QgsVertexMarker
from .comparison_core import validate_pair,key
from .comparison_spatial import calculate
from .route_plot_ab import ABRoutePlot


class ABSpatialPanel:
    def __init__(self,handler,comparison):
        self.handler=handler;self.parent=comparison;self.dock=None
        self.tool=None;self.prior=None;self.band=None;self.marker=None
        self.vertices=[];self.crs=None;self.result=None;self.selected_key=None

    def build(self,dock,layout):
        self.dock=dock
        group=QGroupBox('Phase 4｜同一点・同一経路・同一区域のA/B数値比較（v0.7.2）')
        lay=QVBoxLayout(group)
        note=QLabel('上で選んだA/Bを、同じ地図位置・経路・区域で比較します。追加のデータ読み込みは不要です。MRTは対応評価点だけを使用。')
        note.setWordWrap(True);lay.addWidget(note)
        self.mode=QComboBox();self.mode.addItem('点比較（クリックで即評価）','point')
        self.mode.addItem('線比較（経路とA/Bグラフ）','route')
        self.mode.addItem('面比較（同一区域）','area')
        lay.addWidget(self.mode)
        row=QHBoxLayout()
        self.radius=QDoubleSpinBox();self.radius.setRange(0.5,100.);self.radius.setDecimals(1);self.radius.setValue(20.);self.radius.setSuffix(' m')
        self.step=QDoubleSpinBox();self.step.setRange(2.,100.);self.step.setValue(10.);self.step.setSuffix(' m')
        self.threshold=QDoubleSpinBox();self.threshold.setRange(-50.,100.);self.threshold.setValue(40.);self.threshold.setSuffix(' °C')
        row.addWidget(QLabel('探索半径'));row.addWidget(self.radius);lay.addLayout(row)
        row=QHBoxLayout();row.addWidget(QLabel('経路間隔'));row.addWidget(self.step)
        row.addWidget(QLabel('高温閾値'));row.addWidget(self.threshold);lay.addLayout(row)
        row=QHBoxLayout();self.start_btn=QPushButton('地図で指定を開始');self.start_btn.clicked.connect(self.start)
        self.undo_btn=QPushButton('1点戻す');self.undo_btn.clicked.connect(self.undo)
        row.addWidget(self.start_btn);row.addWidget(self.undo_btn);lay.addLayout(row)
        row=QHBoxLayout();self.finish_btn=QPushButton('経路・区域を確定して比較');self.finish_btn.clicked.connect(self.finish)
        self.cancel_btn=QPushButton('操作終了');self.cancel_btn.clicked.connect(self.cancel)
        row.addWidget(self.finish_btn);row.addWidget(self.cancel_btn);lay.addLayout(row)
        self.status=QLabel('A/Bと比較方法を選んで開始してください。')
        self.status.setWordWrap(True);lay.addWidget(self.status)
        self.plot=ABRoutePlot();lay.addWidget(self.plot);self.plot.hide()
        self.text=QPlainTextEdit();self.text.setReadOnly(True);self.text.setMinimumHeight(220);lay.addWidget(self.text)
        self.save_btn=QPushButton('A/B評価結果をCSV＋JSONで保存…');self.save_btn.clicked.connect(self.save);lay.addWidget(self.save_btn)
        warning=QLabel('MRT：同一point_id・XY・高さが一致する評価点だけを比較し、補間しません。Surfaceの点は同じXYのパッチ値、区域は個別の面積加重平均の差です。WALL・HIP・Surface線比較は対象外。')
        warning.setWordWrap(True);lay.addWidget(warning)
        layout.addWidget(group)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.mode_changed()

    def selection_changed(self):
        if self.dock is None:return
        new=(self.parent.kind.currentData(),self.parent.a.currentData(),self.parent.b.currentData())
        if new!=self.selected_key:
            self.cancel();self.selected_key=new;self.result=None
            self.text.clear();self.plot.set_rows([])
            self.status.setText('A/Bの選択が変更されました。新しい条件で作図してください。')
        self.sync()

    def mode_changed(self,*args):
        if self.dock is None:return
        self.cancel();self.result=None;self.text.clear();self.plot.set_rows([])
        mode=self.mode.currentData()
        self.radius.setEnabled(mode in ('point','route') and self.parent.kind.currentData()=='mrt')
        self.step.setEnabled(mode=='route')
        self.threshold.setEnabled(mode=='area')
        self.plot.setVisible(mode=='route')
        self.status.setText('地図で指定を開始してください。点はクリックだけで評価。線は2点以上、面は3点以上で確定します。')
        self.sync()

    def sync(self):
        if self.dock is None:return
        a,b=self.parent.pair();valid=False
        try:
            kind=validate_pair(a,b)
            valid=not (kind=='surface' and self.mode.currentData()=='route')
        except ValueError:pass
        drawing=self.tool is not None
        self.start_btn.setEnabled(valid and not drawing)
        self.undo_btn.setEnabled(drawing and len(self.vertices)>0 and self.mode.currentData()!='point')
        minimum=2 if self.mode.currentData()=='route' else 3
        self.finish_btn.setEnabled(drawing and self.mode.currentData()!='point' and len(self.vertices)>=minimum)
        self.cancel_btn.setEnabled(drawing)
        self.save_btn.setEnabled(self.result is not None and (not drawing or self.mode.currentData()=='point'))
        self.mode.setEnabled(not drawing)
        self.radius.setEnabled(not drawing and self.mode.currentData() in ('point','route') and self.parent.kind.currentData()=='mrt')
        self.step.setEnabled(not drawing and self.mode.currentData()=='route')
        self.threshold.setEnabled(not drawing and self.mode.currentData()=='area')
        if not valid and not drawing:
            self.start_btn.setToolTip('同じ種類のA/Bを選んでください。Surfaceの経路比較は未対応です。')

    def _clear_geom(self):
        if self.band is not None:
            try:self.handler.iface.mapCanvas().scene().removeItem(self.band)
            except RuntimeError:pass
            self.band=None
        if self.marker is not None:
            try:self.handler.iface.mapCanvas().scene().removeItem(self.marker)
            except RuntimeError:pass
            self.marker=None

    def _release(self):
        if self.tool is None:return
        tool=self.tool;self.tool=None
        canvas=self.handler.iface.mapCanvas()
        try:tool.canvasClicked.disconnect(self.clicked)
        except (TypeError,RuntimeError):pass
        if canvas.mapTool() is tool:
            try:
                if self.prior is not None and self.prior is not tool:canvas.setMapTool(self.prior)
                else:self.handler.iface.actionPan().trigger()
            except RuntimeError:self.handler.iface.actionPan().trigger()
        self.prior=None;tool.deleteLater()
        self.sync()

    def cancel(self,*args):
        self._release();self.vertices=[];self._clear_geom()
        self.sync()

    def invalidate(self):
        self.cancel();self.result=None
        if self.dock is not None:
            self.text.clear();self.plot.set_rows([])
            self.status.setText('解析レイヤが更新されました。対象を確認して再評価してください。');self.sync()

    def start(self,*args):
        if self.tool is not None or self.handler.running:return
        a,b=self.parent.pair()
        try:
            kind=validate_pair(a,b)
            if kind=='surface' and self.mode.currentData()=='route':
                raise ValueError('Surfaceの線比較は未実装です。点か面を選んでください。')
        except Exception as exc:
            self.status.setText(str(exc));return
        self.handler.area.cancel();self.handler.area.remove_band()
        self.handler.stop_point_evaluation();self.handler.cancel_route_evaluation()
        self.handler._remove_route_band()
        if self.handler._point_marker is not None:
            try:self.handler.iface.mapCanvas().scene().removeItem(self.handler._point_marker)
            except RuntimeError:pass
            self.handler._point_marker=None
        canvas=self.handler.iface.mapCanvas();self.crs=canvas.mapSettings().destinationCrs()
        self.vertices=[];self.result=None;self.text.clear();self.plot.set_rows([]);self._clear_geom()
        mode=self.mode.currentData()
        if mode!='point':
            geom=QgsWkbTypes.LineGeometry if mode=='route' else QgsWkbTypes.PolygonGeometry
            self.band=QgsRubberBand(canvas,geom)
            self.band.setColor(QColor(38,153,207,50));self.band.setStrokeColor(QColor('#16c7e2'));self.band.setWidth(3)
        self.prior=canvas.mapTool();self.tool=QgsMapToolEmitPoint(canvas)
        self.tool.canvasClicked.connect(self.clicked);canvas.setMapTool(self.tool)
        self.status.setText('点をクリックしてください。' if mode=='point' else '頂点を順にクリックし、完了したら「確定して比較」。')
        self.sync()

    def clicked(self,p,button):
        if self.tool is None or button!=Qt.LeftButton:return
        canvas=self.handler.iface.mapCanvas()
        if canvas.mapSettings().destinationCrs()!=self.crs:
            self.cancel();self.status.setText('地図の座標系が変わりました。操作をやり直してください。');return
        xy=(float(p.x()),float(p.y()))
        if self.mode.currentData()=='point':
            self.vertices=[xy]
            if self.marker is None:
                self.marker=QgsVertexMarker(canvas);self.marker.setColor(QColor('#e43024'))
                self.marker.setIconSize(13);self.marker.setPenWidth(2)
            self.marker.setCenter(p)
            self._calculate()  # Keep point tool active so the next click can compare again.
            return
        if len(self.vertices)>=200:
            self.status.setText('頂点上限200です。経路・区域を確定してください。');return
        if self.vertices and self.vertices[-1]==xy:return
        self.vertices.append(xy)
        self.band.reset(QgsWkbTypes.LineGeometry if self.mode.currentData()=='route' else QgsWkbTypes.PolygonGeometry)
        for x,y in self.vertices:self.band.addPoint(QgsPointXY(x,y),True)
        self.status.setText(f'頂点数：{len(self.vertices)}');self.sync()

    def undo(self,*args):
        if self.tool and self.vertices and self.mode.currentData()!='point':
            self.vertices.pop();self.band.reset(QgsWkbTypes.LineGeometry if self.mode.currentData()=='route' else QgsWkbTypes.PolygonGeometry)
            for x,y in self.vertices:self.band.addPoint(QgsPointXY(x,y),True)
            self.status.setText(f'頂点数：{len(self.vertices)}');self.sync()

    def finish(self,*args):
        if self.tool and self.mode.currentData()!='point':self._calculate()

    def _calculate(self):
        a,b=self.parent.pair()
        # Prevent silently comparing a different A/B selection during drawing.
        if (self.parent.kind.currentData(),self.parent.a.currentData(),self.parent.b.currentData())!=self.selected_key:
            self.cancel();self.status.setText('A/Bが変更されました。作図をやり直してください。');return
        self.result=None;self.save_btn.setEnabled(False)
        self.status.setText('共通位置のA/B温度を評価中…');self.status.repaint()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result=calculate(a,b,self.mode.currentData(),self.vertices,self.crs,
                             self.radius.value(),self.step.value(),self.threshold.value())
            self.result=result;self.text.setPlainText(result['report'])
            self.plot.set_rows(result['graph'])
            if self.mode.currentData()!='point':self._release()
            self.status.setText('A/B評価が完了しました。CSV＋JSON保存ができます。')
        except Exception as exc:
            self.status.setText('A/B評価エラー：'+str(exc))
            self.dock.write('Phase 4 点・線・面比較 ERROR: '+traceback.format_exc())
        finally:
            QApplication.restoreOverrideCursor();self.sync()

    def save(self,*args):
        if self.result is None:return
        directory=os.path.dirname(QgsProject.instance().fileName() or '') or os.path.expanduser('~')
        filename='Campus3D_AB_'+self.mode.currentData()+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv'
        path,_=QFileDialog.getSaveFileName(self.dock,'Campus3D A/B空間比較CSV',os.path.join(directory,filename),'CSV (*.csv)')
        if not path:return
        if not path.lower().endswith('.csv'):path+='.csv'
        other=os.path.splitext(path)[0]+'_report.json'
        if os.path.exists(path) or os.path.exists(other):
            QMessageBox.warning(self.dock,'Campus3D','同名の比較結果があります。別名を指定してください（上書きしません）。');return
        try:
            audit=dict(self.result['audit'])
            audit['report']=self.result['report']
            audit['created_local']=datetime.now().isoformat(timespec='seconds')
            fields=['mode','kind','crs','timestamp_A','timestamp_B','source_A','source_B',
                    'point_id','role','distance_m','x_m','y_m','nearest_distance_m','z_a_m','z_b_m',
                    'a_count','b_count','a_hot_pct','b_hot_pct','a_covered_m2','b_covered_m2','region_area_m2','geometry_wkt',
                    'a_c','b_c','delta_c','status']
            with open(path,'x',encoding='utf-8-sig',newline='') as f:
                w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
                for row in self.result['rows']:
                    fixed=dict(mode=audit['mode'],kind=audit['kind'],crs=audit['crs'],
                               timestamp_A=audit['timestamp_A'],timestamp_B=audit['timestamp_B'],
                               source_A=audit['A'],source_B=audit['B'],geometry_wkt=audit.get('geometry_wkt',''));fixed.update(row)
                    w.writerow(fixed)
            with open(other,'x',encoding='utf-8') as f:json.dump(audit,f,ensure_ascii=False,indent=2)
            self.status.setText('CSV・JSON保存：'+path)
            self.dock.write('Phase 4空間比較：'+path)
        except Exception as exc:
            # keep consistent result pair: never keep half the outputs on failure
            for candidate in (path,other):
                try:os.remove(candidate)
                except OSError:pass
            QMessageBox.warning(self.dock,'Campus3D','比較結果の保存に失敗しました：'+str(exc))

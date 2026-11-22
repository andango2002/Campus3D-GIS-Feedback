# -*- coding: utf-8 -*-
"""Phase 4.2: in-place A/B loading, common scales, strict MRT delta and spatial A/B."""
import os
from datetime import datetime
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,
                                 QPushButton,QMessageBox,QFileDialog)
from qgis.core import QgsProject
from . import visualization as viz
from .map_colorbar import same_renderer_classes
from .comparison_core import key, role, validate_pair, common_limits, matching_cuts, visible_side
from .comparison_delta import make_diff_layer
from .comparison_spatial_panel import ABSpatialPanel


class ComparisonPanel:
    def __init__(self, handler):
        self.handler=handler; self.dock=None; self.entries={}; self.applied=None
        self.diff_layer_id=None; self.diff_cuts=None; self.diff_subtitle=None; self.diff_stats=None
        self.spatial=ABSpatialPanel(handler,self)

    def build(self,dock,layout):
        self.dock=dock
        group=QGroupBox('Phase 4｜A/B比較：読込・共通温度スケール・差分（BETA）')
        lay=QVBoxLayout(group)
        info=QLabel('この欄だけでA/Bのmanifestを追加できます。読込済みデータを選ぶだけでもOK。表示比較はMRT/Surface、差分マップは同位置のMRTに対応します。')
        info.setWordWrap(True); lay.addWidget(info)
        self.kind=QComboBox(); self.kind.addItem('MRT（平均放射温度）','mrt'); self.kind.addItem('Surface Temperature（表面温度）','surface'); lay.addWidget(self.kind)
        self.a=QComboBox(); self.b=QComboBox()
        for side, label, combo in [('A','A｜基準',self.a),('B','B｜比較対象',self.b)]:
            lay.addWidget(QLabel(label)); combo.setMinimumContentsLength(15); combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); lay.addWidget(combo)
            load=QPushButton(side+'の解析manifestを読み込む…')
            load.setToolTip('A/B比較の画面を離れずに、MRT_tNNNまたはSURFACE_tNNNのgis_manifest.jsonを読み込みます。')
            load.clicked.connect(lambda checked=False, v=side: self.load_side(v))
            lay.addWidget(load)
        self.detail=QLabel(); self.detail.setWordWrap(True); lay.addWidget(self.detail)
        self.refresh_btn=QPushButton('比較対象を更新'); self.refresh_btn.clicked.connect(self.rescan); lay.addWidget(self.refresh_btn)
        self.apply_btn=QPushButton('共通スケールを適用してAを表示'); self.apply_btn.clicked.connect(lambda: self.show('A')); lay.addWidget(self.apply_btn)
        row=QHBoxLayout(); self.show_a=QPushButton('Aを表示'); self.show_b=QPushButton('Bを表示'); self.show_a.clicked.connect(lambda: self.show('A')); self.show_b.clicked.connect(lambda: self.show('B')); row.addWidget(self.show_a); row.addWidget(self.show_b); lay.addLayout(row)
        self.diff_btn=QPushButton('ΔMRT 差分マップを作成（B−A）…')
        self.diff_btn.clicked.connect(self.create_delta); lay.addWidget(self.diff_btn)
        self.diff_info=QLabel('MRT：point_id・XY座標・評価高さが一致する点のみ差を計算。異なる格子は補間しません。Surfaceの差分マップは未実装。v0.7.2の点・面評価では温度値や平均値の差を比較します。')
        self.diff_info.setWordWrap(True); lay.addWidget(self.diff_info)
        self.status=QLabel(); self.status.setWordWrap(True); lay.addWidget(self.status)
        note=QLabel('A/Bの全温度範囲を自動採用。選択外のMRT/Surfaceは非表示にします（他の地図レイヤは従来どおり）。A/Bの日時が異なる場合、差分は時間変化であり設計効果ではありません。')
        note.setWordWrap(True); lay.addWidget(note); layout.addWidget(group)
        self.spatial.build(dock,layout)
        self.kind.currentIndexChanged.connect(self.refresh); self.a.currentIndexChanged.connect(self.changed); self.b.currentIndexChanged.connect(self.changed)
        self.refresh()


    def load_side(self, side):
        """Import an A/B source without scrolling to the top dock controls."""
        if self.dock is None or self.handler.running:return
        current=self.a.currentData() if side=='A' else self.b.currentData()
        start=current[1] if current and os.path.isdir(current[1]) else self.dock.last_folder
        if not start or not os.path.isdir(start):
            start=os.path.expanduser('~')
        path,_=QFileDialog.getOpenFileName(self.dock,side+'｜Campus3D解析結果を選択（gis_manifest.json）',
                                          start,'GIS manifest (gis_manifest.json);;JSON (*.json)')
        if not path:return
        try:
            # Same validation as the established import pipeline.
            from .plugin import _safe_manifest
            kind=self.kind.currentData()
            path,meta=_safe_manifest(path,kind)
            self.dock.set_path(kind,path)
            self.handler.run(kind)
            self.dock.refresh_visualization(select_kind=kind)
            selected=(kind,os.path.normcase(os.path.realpath(os.path.dirname(path))),str(meta['timestamp_jst']))
            combo=self.a if side=='A' else self.b
            index=combo.findData(selected)
            if index<0:
                raise ValueError('解析結果がレイヤ一覧にありません。上記ログに読込エラーがないか確認してください。')
            combo.setCurrentIndex(index)
            self.status.setText(side+'を読み込み、比較対象に設定しました。')
        except Exception as exc:
            QMessageBox.warning(self.dock,'Campus3D A/B読込',str(exc))

    def create_delta(self):
        """A/B are trusted read-only QGIS layers; output a new GPKG/CSV/JSON."""
        if self.dock is None or self.handler.running:return
        try:
            a,b=self.pair(); kind=validate_pair(a,b)
            if kind!='mrt':
                raise ValueError('v0.7.1の差分マップはMRTのみです。Surfaceの面積交差による差分は次版で実装します。')
            project=QgsProject.instance()
            canvas=self.handler.iface.mapCanvas()
            if hasattr(canvas,'theme') and canvas.theme():
                raise ValueError('QGISの地図テーマ固定を解除してください。')
            folder=project.homePath()
            if not folder or not os.path.isdir(folder):folder=os.path.expanduser('~')
            suggested=os.path.join(folder,'Campus3D_DeltaMRT_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.gpkg')
            destination,_=QFileDialog.getSaveFileName(self.dock,'差分マップの保存先（GeoPackage＋CSV＋JSON）',
                                                      suggested,'GeoPackage (*.gpkg)')
            if not destination:return
            if not destination.lower().endswith('.gpkg'):destination+='.gpkg'
            layer,meta,cuts=make_diff_layer(a,b,destination)
            project.addMapLayer(layer)
            self.diff_layer_id=layer.id();self.diff_cuts=cuts
            self.diff_subtitle=('A '+a[2][5:16].replace('T',' ')+' → B '+b[2][5:16].replace('T',' ')+
                               (' JST' if '+09:00' in a[2] and '+09:00' in b[2] else ''))
            self.diff_stats=meta['statistics']
            layer.setCustomProperty('Campus3D/delta_subtitle',self.diff_subtitle)
            # Hide only known temperature datasets. Other GIS layers stay untouched.
            root=project.layerTreeRoot()
            for e in self.dock._viz_sets:
                for l in e[3]:
                    node=root.findLayer(l.id())
                    if node:node.setItemVisibilityChecked(False)
            diff_node=root.findLayer(layer.id())
            if diff_node:diff_node.setItemVisibilityCheckedParentRecursive(True)
            s=meta['statistics'];self.applied=None
            self.diff_info.setText('ΔMRT=B−A｜一致点 %d（Aの%.1f%%／Bの%.1f%%）／不一致ID %d+%d／座標不一致 %d／高さ不一致 %d｜平均 %+.2f℃｜最小 %+.2f℃／最大 %+.2f℃\nGeoPackage・CSV・JSONを保存しました。'%(
                s['matched'],s['matched_pct_of_A'],s['matched_pct_of_B'],s['only_A'],s['only_B'],
                s['same_id_xy_mismatch'],s['same_id_height_mismatch'],s['delta_mean_c'],s['delta_min_c'],s['delta_max_c']))
            caution=('【要注意：一致率95%未満。差分は共通位置だけです】 ' if
                     min(s['matched_pct_of_A'],s['matched_pct_of_B'])<95 else '')
            self.status.setText(caution+'ΔMRTを表示中。負＝Bの温度低下、正＝Bの温度上昇。異時刻比較は時間変化です。')
            self.dock.viz_follow_visibility.setChecked(True)
            self.dock.viz_map_colorbar.setChecked(True)
            canvas.refresh(); self.handler._queue_visibility_refresh();self.handler.update_map_colorbar()
            self.dock.write('Phase 4 ΔMRT: '+destination+'\n'+self.diff_info.text())
            self.handler.iface.messageBar().pushSuccess('Campus3D GIS Feedback','ΔMRT差分マップを作成しました。')
        except Exception as exc:
            QMessageBox.warning(self.dock,'Campus3D ΔMRT比較',str(exc))

    def delta_legend(self, ordered_ids):
        # Derive the legend from the saved layer's real renderer. This also works
        # after reopening a QGZ: the layer custom properties persist in QGIS.
        regular={l.id() for e in self.dock._viz_sets for l in e[3]}
        project=QgsProject.instance()
        for lid in ordered_ids:
            if lid in regular:return None  # Regular temperature is above delta.
            layer=project.mapLayer(lid)
            if layer is None or not layer.isValid() or not layer.customProperty('Campus3D/delta',False):
                continue
            cuts, warning=same_renderer_classes([layer],'delta_c')
            if warning:return None
            return cuts,str(layer.customProperty('Campus3D/delta_subtitle','A → B'))
        return None

    def rescan(self,*args):
        self.dock.refresh_visualization()

    def refresh(self,*args):
        if self.dock is None: return
        old=(self.a.currentData(),self.b.currentData())
        self.entries={key(e):e for e in self.dock._viz_sets if e[0]==self.kind.currentData()}
        keys=list(self.entries)
        for j,combo in enumerate((self.a,self.b)):
            combo.blockSignals(True); combo.clear()
            for k in keys:
                folder=k[1].replace('\\','/'); parts=folder.rstrip('/').split('/')
                # keep model name when long paths share same final folder
                model=next((p for p in reversed(parts) if 'campus3d_blender' in p.lower()),None)
                short=(model+' / '+parts[-1]) if model else '/'.join(parts[-3:])
                combo.addItem(f'{k[2]} | {short}',k)
                combo.setItemData(combo.count()-1,folder,Qt.ToolTipRole)
            choice=keys.index(old[j]) if old[j] in keys else min(j,len(keys)-1)
            combo.setCurrentIndex(choice); combo.blockSignals(False)
        self.changed()

    def pair(self):
        return self.entries.get(self.a.currentData()),self.entries.get(self.b.currentData())

    def changed(self,*args):
        if self.dock is None:return
        self.applied=None
        a,b=self.pair()
        self.detail.setText('\n'.join(f'{label}：{e[1]}' for label,e in [('A',a),('B',b)] if e))
        try:
            validate_pair(a,b)
            note='同時刻の比較（解析条件は要確認）。' if a[2]==b[2] else '異なる日時の比較です。設計効果だけの差とは解釈できません。'
            self.status.setText(note+' 「共通スケールを適用してAを表示」を押してください。'); ok=True
        except ValueError as exc:
            self.status.setText(str(exc) if len(self.entries)>=2 else '同種の解析結果が2つ必要です。上の読み込み欄で別の日時またはモデルのmanifestを読み込んでください。'); ok=False
        for btn in (self.apply_btn,self.show_a,self.show_b):btn.setEnabled(ok)
        self.diff_btn.setEnabled(ok and a is not None and a[0]=='mrt')
        self.spatial.selection_changed()
        self.handler.update_map_colorbar()

    def show(self, side):
        if self.dock is None or self.handler.running:return
        try:
            a,b=self.pair(); kind=validate_pair(a,b)
            all_entries=self.dock._viz_sets
            root=QgsProject.instance().layerTreeRoot(); canvas=self.handler.iface.mapCanvas()
            if hasattr(canvas,'theme') and canvas.theme():
                raise ValueError('地図テーマが固定されています。テーマ固定を解除してから比較してください。')
            target=a if side=='A' else b
            target_ids={l.id() for l in target[3]}
            all_ids={l.id() for e in all_entries for l in e[3]}
            nodes={i:root.findLayer(i) for i in all_ids}
            if any(node is None for node in nodes.values()):raise ValueError('レイヤツリーにない温度レイヤがあります。比較対象を更新してください。')
            field='surface_c' if kind=='surface' else 'mrt_c'
            layers=list(a[3])+list(b[3]); lo,hi=common_limits(*viz.data_range(layers,field))
            colors=viz.SURFACE_COLORS if kind=='surface' else viz.MRT_COLORS
            cuts=viz.breaks(lo,hi,colors)
            actual_a,_=same_renderer_classes(a[3],field); actual_b,_=same_renderer_classes(b[3],field)
            if not matching_cuts(actual_a,cuts) or not matching_cuts(actual_b,cuts):
                viz.apply_style(layers,kind,lo,hi)
                for layer in layers: self.handler.iface.layerTreeView().refreshLayerSymbology(layer.id())
            self.applied=(key(a),key(b),cuts)
            # A/B selection must hide the derived Δ layer (never delete its saved GPKG).
            # Hide all previously generated delta layers, including those
            # restored from a saved QGZ after plugin restart.
            for candidate in QgsProject.instance().mapLayers().values():
                if candidate.customProperty('Campus3D/delta',False):
                    previous_delta=root.findLayer(candidate.id())
                    if previous_delta:previous_delta.setItemVisibilityChecked(False)
            # Switch only recognized temperature layers; don't change map extent.
            for i,node in nodes.items():
                if i not in target_ids:node.setItemVisibilityChecked(False)
            for i in target_ids:nodes[i].setItemVisibilityCheckedParentRecursive(True)
            self.status.setText(f'{side}への切替を要求しました。地図の表示状態を確認中…')
            self.dock.viz_follow_visibility.setChecked(True)
            self.dock.viz_map_colorbar.setChecked(True)
            canvas.refresh(); self.handler._queue_visibility_refresh(); self.handler.update_map_colorbar()
            self.dock.write(f'Phase 4 A/B: {side} | {kind} {lo:.1f}～{hi:.1f} °C\nA: {a[1]} | {a[2]}\nB: {b[1]} | {b[2]}')
        except Exception as exc:
            self.applied=None; self.status.setText(str(exc)); self.handler.update_map_colorbar()
            QMessageBox.warning(self.dock,'Campus3D A/B比較',str(exc))

    def legend_context(self,visible_ids,cuts=None):
        """Called from existing visualization refresh; don't label mismatched renderers."""
        if self.dock is None or not self.applied:return ''
        a,b=self.pair()
        if not a or not b or (key(a),key(b))!=self.applied[:2]:return ''
        field='surface_c' if a[0]=='surface' else 'mrt_c'
        expected=self.applied[2]
        ca,_=same_renderer_classes(a[3],field); cb,_=same_renderer_classes(b[3],field)
        if not matching_cuts(ca,expected) or not matching_cuts(cb,expected):
            self.status.setText('A/Bの温度階級が変更されました。共通スケールを再適用してください。'); return ''
        side=visible_side({l.id() for l in a[3]},{l.id() for l in b[3]},visible_ids,
                         {l.id() for e in self.dock._viz_sets for l in e[3]})
        if not side:
            self.status.setText('A/B以外の温度レイヤ、または一部の面が表示されています。「Aを表示」「Bを表示」で切り替えてください。'); return ''
        self.status.setText(f'{side} 表示中｜共通スケール {expected[0][0]:.1f}～{expected[-1][1]:.1f} °C'+ ('｜異なる日時' if a[2]!=b[2] else '｜同時刻（他条件要確認）'))
        if cuts is not None and not matching_cuts(cuts,expected):return ''
        return side

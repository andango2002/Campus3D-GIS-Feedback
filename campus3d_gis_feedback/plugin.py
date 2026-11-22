# -*- coding: utf-8 -*-
"""Campus3D GIS Feedback v0.7.2: an independent QGIS dock-panel plugin.

The six computational routines in engines/ are minimally adapted copies of
previous Campus3D QGIS console tools, rather than reimplemented algorithms.
This plugin does NOT create or change the Blender Post-Sim source results.
"""
import csv
import json
import os
import traceback
from datetime import datetime

from qgis.PyQt.QtCore import Qt, QSettings, QUrl, QTimer
from qgis.PyQt.QtGui import QDesktopServices, QColor
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGroupBox, QLineEdit, QPushButton, QScrollArea, QFileDialog,
    QMessageBox, QPlainTextEdit, QComboBox, QDoubleSpinBox, QCheckBox, QApplication,
)
from qgis.core import (QgsProject, QgsVectorLayer, QgsGraduatedSymbolRenderer,
                       QgsRendererRange, QgsFillSymbol, QgsMarkerSymbol,
                       QgsPointXY, QgsWkbTypes)
from .engines import import_mrt, import_surface, import_hip
from .engines import coverage_audit, regional_hip, regional_context
from . import visualization
from .map_colorbar import MapTemperatureColorbar, same_renderer_classes
from .visibility_policy import select_dataset
from .point_evaluation_core import build_point_datasets
from .point_evaluation import evaluate_point
from .route_profile import evaluate_route
from .route_plot import MrtRoutePlot
from .area_panel import AreaPanel
from .comparison_panel import ComparisonPanel
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker, QgsRubberBand

VERSION = '0.7.2'
GROUP_NAME = 'Campus3D | GIS Feedback'
SCHEMAS = {
    'mrt': 'Campus3D_GIS_Feedback_v0.1',
    'surface': 'Campus3D_GIS_Feedback_Surface_v0.2',
    'hip': 'Campus3D_GIS_Feedback_HIP_v0.1',
    'regional': 'Campus3D_Regional_HIP_v0.1',
}
EXPECTED_QUANTITY = {'mrt': 'MRT', 'surface': 'SURFACE', 'hip': 'HIP'}
INPUT_LABELS = {
    'mrt': 'MRT_tNNN / gis_manifest.json',
    'surface': 'SURFACE_tNNN / gis_manifest.json',
    'hip': 'HIP_tNNN / gis_manifest.json',
    'regional': 'Regional_HIP_* / regional_hip_manifest.json',
}


def _safe_manifest(path, kind):
    """Validate exact manifest kind before passing to established engine."""
    path = os.path.normpath(os.path.abspath(os.path.expanduser(path)))
    if not os.path.isfile(path):
        raise ValueError('Manifestファイルが見つかりません: ' + path)
    if os.path.basename(path).lower() != ('regional_hip_manifest.json' if kind == 'regional' else 'gis_manifest.json'):
        raise ValueError('選択したファイル名が対象と一致しません: ' + INPUT_LABELS[kind])
    with open(path, 'r', encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get('schema') != SCHEMAS[kind]:
        raise ValueError('manifestの種類が異なります。必要: ' + SCHEMAS[kind])
    if kind in EXPECTED_QUANTITY and data.get('quantity') != EXPECTED_QUANTITY[kind]:
        raise ValueError('manifestの解析指標が一致しません')
    return path, data


def _subfolders(parent, prefix):
    if not os.path.isdir(parent):
        return set()
    return {os.path.join(parent, name) for name in os.listdir(parent)
            if name.startswith(prefix) and os.path.isdir(os.path.join(parent, name))}


def _existing_gpkg_specs(kind, data, folder):
    if kind == 'mrt':
        return [(os.path.join(folder, 'mrt_points.gpkg'), 'mrt_points',
                 'Campus3D MRT ' + str(data.get('timestamp_jst')), int(data.get('point_count', 0)))]
    if kind == 'hip':
        return [(os.path.join(folder, 'hip_domain_provisional.gpkg'), 'hip_domain_provisional',
                 'Campus3D HIP PROVISIONAL ' + str(data.get('timestamp_jst')), 1)]
    if kind == 'surface':
        counts = data.get('patch_counts', {})
        return [(os.path.join(folder, 'surface_' + role.lower() + '.gpkg'),
                 'surface_' + role.lower(),
                 'Campus3D Surface ' + role + ' ' + str(data.get('timestamp_jst')),
                 int(counts.get(role, 0))) for role in ('GROUND','ROOF','WALL')
                if int(counts.get(role, 0)) > 0]
    raise ValueError('Unsupported import type')


def _canonical_data_path(source):
    return os.path.normcase(os.path.realpath(os.path.normpath(source.split('|', 1)[0])))



def _style_loaded_layers(kind, layers, data, references=None):
    """Apply familiar shared scales when reloading GPKGs from a previous session."""
    if not layers:
        return
    if kind == 'hip':
        for layer in layers:
            layer.renderer().setSymbol(QgsFillSymbol.createSimple({
                'style':'no', 'outline_style':'dash',
                'outline_color':'190,25,190,255',
                'outline_width':'0.8','outline_width_unit':'MM'}))
            layer.triggerRepaint()
        return
    if kind == 'surface':
        # Match the six-color scheme of the original v0.4.1 importer.
        ranges = []
        for layer in (references if references is not None else layers):
            idx = layer.fields().indexFromName('surface_c')
            if idx < 0:
                raise ValueError('Existing Surface GPKG missing surface_c attribute')
            ranges.append((float(layer.dataProvider().minimumValue(idx)),
                           float(layer.dataProvider().maximumValue(idx))))
        # Prefer global source values from all present GPkg files, not selected layer.
        if not ranges:
            return
        low = min(r[0] for r in ranges)
        high = max(r[1] for r in ranges)
        if high - low < .001:
            low -= 1.0
            high += 1.0
        colors = ('#2369d4','#30bfe2','#55c58b','#eee057','#ee9135','#d84c38')
        for layer in layers:
            kind_wall = 'surface_wall' in layer.source().lower()
            segments = []
            for i, col in enumerate(colors):
                a = low+(high-low)*i/len(colors)
                b = low+(high-low)*(i+1)/len(colors)
                symbol = (QgsMarkerSymbol.createSimple({'name':'circle','color':col,
                        'outline_style':'no','size':'1.7'}) if kind_wall else
                        QgsFillSymbol.createSimple({'color':col,'outline_style':'no'}))
                segments.append(QgsRendererRange(a,b,symbol,f'{a:.1f} - {b:.1f} C'))
            layer.setRenderer(QgsGraduatedSymbolRenderer('surface_c',segments))
            layer.triggerRepaint()
        return
    if kind == 'mrt':
        colors = ('#4c1070','#16bad8','#95ee48','#f58b36','#a82429')
        for layer in layers:
            idx = layer.fields().indexFromName('mrt_c')
            if idx < 0:
                raise ValueError('Existing MRT GPKG missing mrt_c')
            low = float(layer.dataProvider().minimumValue(idx))
            high = float(layer.dataProvider().maximumValue(idx))
            if high-low < .001:
                low -= 1.0
                high += 1.0
            cutoffs = [low+(high-low)*i/len(colors) for i in range(len(colors)+1)]
            segments = []
            for i,col in enumerate(colors):
                symbol = QgsMarkerSymbol.createSimple({'name':'circle','color':col,
                          'outline_style':'no','size':'1.7'})
                segments.append(QgsRendererRange(cutoffs[i],cutoffs[i+1],symbol,
                                                f'{cutoffs[i]:.1f} - {cutoffs[i+1]:.1f}'))
            layer.setRenderer(QgsGraduatedSymbolRenderer('mrt_c',segments))
            layer.triggerRepaint()


class Campus3DFeedbackDock(QDockWidget):
    def __init__(self, parent, handler):
        super().__init__('Campus3D GIS Feedback v' + VERSION, parent)
        self.setObjectName('Campus3DGISFeedbackDock_v050')
        self.handler = handler
        self.settings = QSettings()
        self.inputs = {}
        self.buttons = []
        self.last_folder = None
        self.last_report = None
        self.setMinimumWidth(365)
        self.resize(425, 690)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setSpacing(8)

        intro = QLabel('Blender Post-Sim出力をQGISへ戻すための統合パネル。\n'
                       '既存のMRT・Surface・HIP・Audit・地域集計を呼び出します。')
        intro.setWordWrap(True)
        lay.addWidget(intro)

        for group_title, elements in (
            ('1｜解析データの読み込み', ['mrt', 'surface', 'hip']),
            ('2｜品質診断と地域集計', ['regional']),
        ):
            group = QGroupBox(group_title)
            outer = QVBoxLayout(group)
            for name in elements:
                outer.addWidget(QLabel(INPUT_LABELS[name]))
                row = QHBoxLayout()
                inp = QLineEdit()
                inp.setPlaceholderText('manifestを指定（未指定なら実行時に選択）')
                inp.setToolTip(INPUT_LABELS[name])
                inp.setText(self.settings.value('Campus3DGISFeedback/v050/' + name, '', type=str))
                row.addWidget(inp, 1)
                choose = QPushButton('参照…')
                choose.setFixedWidth(64)
                choose.clicked.connect(lambda checked=False, k=name: self.choose(k))
                row.addWidget(choose)
                outer.addLayout(row)
                self.inputs[name] = inp
            if group_title.startswith('1'):
                for text, operation, helptext in (
                    ('MRTを読み込む', 'mrt', 'MRT評価点をGeoPackage化'),
                    ('Surface Temperatureを読み込む', 'surface', 'GROUND / ROOF / WALLを読込'),
                    ('暫定HIPを読み込む', 'hip', '全域HIPの枠線を読込'),
                ):
                    self._add_button(outer, text, operation, helptext)
            else:
                self._add_button(outer, 'Surface Coverage Auditを実行', 'audit',
                                 '2m被覆検査と2Dクリック診断（Surface manifest使用）')
                self._add_button(outer, 'Regional HIPを計算', 'regional_calc',
                                 '100m/200m格子または選択地域（Surface manifest使用）')
                self._add_button(outer, 'Regional HIP Contextを分析', 'context',
                                 '建物・道路・緑地を明示選択（Regional manifest使用）')
            lay.addWidget(group)

        # Phase 2 controls. Display only: source GPKGs and simulations remain intact.
        viz_group = QGroupBox('Phase 2｜Visualization（表示のみ）')
        viz_layout = QVBoxLayout(viz_group)
        viz_layout.addWidget(QLabel('既存のMRT／Surfaceレイヤの温度スケールを統一・調整'))
        self.viz_target = QComboBox()
        self.viz_target.setMinimumWidth(250)
        self.viz_target.currentIndexChanged.connect(self._viz_target_changed)
        viz_layout.addWidget(self.viz_target)
        auto_row = QHBoxLayout()
        self.viz_auto = QCheckBox('対象データの最小・最大値を使用（自動）')
        self.viz_auto.setChecked(True)
        self.viz_auto.toggled.connect(self._viz_auto_changed)
        auto_row.addWidget(self.viz_auto)
        viz_layout.addLayout(auto_row)
        limit_row = QHBoxLayout()
        self.viz_min = QDoubleSpinBox()
        self.viz_max = QDoubleSpinBox()
        for control in (self.viz_min, self.viz_max):
            control.setRange(-273.0, 1000.0)
            control.setDecimals(1)
            control.setSingleStep(0.5)
            control.setSuffix(' °C')
            control.setEnabled(False)
        self.viz_min.setValue(20.0)
        self.viz_max.setValue(50.0)
        limit_row.addWidget(QLabel('下限'))
        limit_row.addWidget(self.viz_min, 1)
        limit_row.addWidget(QLabel('上限'))
        limit_row.addWidget(self.viz_max, 1)
        viz_layout.addLayout(limit_row)
        buttons_row = QHBoxLayout()
        refresh_btn = QPushButton('対象レイヤを更新')
        refresh_btn.clicked.connect(self.refresh_visualization)
        buttons_row.addWidget(refresh_btn)
        self.viz_apply = QPushButton('温度表示を適用')
        self.viz_apply.clicked.connect(self.handler.apply_visualization)
        buttons_row.addWidget(self.viz_apply)
        viz_layout.addLayout(buttons_row)
        self.viz_legend = QLabel('既存のMRT／Surfaceレイヤを読み込んでから、対象レイヤを更新してください。')
        self.viz_legend.setWordWrap(True)
        viz_layout.addWidget(self.viz_legend)
        # Map legend uses *actual QGIS renderer classes* rather than the
        # (possibly not yet applied) values in the input spinboxes.
        self.viz_map_colorbar = QCheckBox('地図上に温度カラーバーを表示')
        self.viz_map_colorbar.setChecked(self.settings.value(
            'Campus3DGISFeedback/v052/map_colorbar_visible', True, type=bool))
        self.viz_map_colorbar.toggled.connect(self.handler.update_map_colorbar)
        viz_layout.addWidget(self.viz_map_colorbar)

        self.viz_follow_visibility = QCheckBox('表示中の最前面温度レイヤに凡例を自動連動')
        self.viz_follow_visibility.setChecked(self.settings.value(
            'Campus3DGISFeedback/v053/follow_visibility', True, type=bool))
        self.viz_follow_visibility.toggled.connect(self._viz_follow_changed)
        viz_layout.addWidget(self.viz_follow_visibility)
        self.viz_target.setEnabled(not self.viz_follow_visibility.isChecked())

        options_row = QHBoxLayout()
        options_row.addWidget(QLabel('凡例位置'))
        self.viz_bar_position = QComboBox()
        for label, value in (('右上', 'top_right'), ('左上', 'top_left'),
                             ('右下', 'bottom_right'), ('左下', 'bottom_left')):
            self.viz_bar_position.addItem(label, value)
        pos = self.settings.value('Campus3DGISFeedback/v053/bar_position', 'top_right', type=str)
        self.viz_bar_position.setCurrentIndex(max(0, self.viz_bar_position.findData(pos)))
        self.viz_bar_position.currentIndexChanged.connect(self.handler.update_map_colorbar)
        options_row.addWidget(self.viz_bar_position, 1)
        options_row.addWidget(QLabel('サイズ'))
        self.viz_bar_size = QComboBox()
        for label, value in (('小', 'compact'), ('中', 'normal'), ('大', 'large')):
            self.viz_bar_size.addItem(label, value)
        sz = self.settings.value('Campus3DGISFeedback/v053/bar_size', 'normal', type=str)
        self.viz_bar_size.setCurrentIndex(max(0, self.viz_bar_size.findData(sz)))
        self.viz_bar_size.currentIndexChanged.connect(self.handler.update_map_colorbar)
        options_row.addWidget(self.viz_bar_size, 1)
        viz_layout.addLayout(options_row)
        self.viz_show_missing_note = QCheckBox('凡例に「温度未描画域」の説明を付ける')
        self.viz_show_missing_note.setChecked(self.settings.value(
            'Campus3DGISFeedback/v054/show_missing_note', True, type=bool))
        self.viz_show_missing_note.toggled.connect(self.handler.update_map_colorbar)
        viz_layout.addWidget(self.viz_show_missing_note)
        self.viz_export_png = QPushButton('地図＋カラーバーをPNG保存…')
        self.viz_export_png.setToolTip('QGISの表示中の地図キャンバスを凡例付きPNGとして保存します。温度やGPKGは変更しません。')
        self.viz_export_png.clicked.connect(self.handler.export_map_png)
        viz_layout.addWidget(self.viz_export_png)
        self.viz_export_note = QLabel(
            '現在の地図キャンバスを画面解像度で保存します（印刷レイアウトとは別）。\n'
            '透明・未描画域がNoDataか未被覆かは表示から断定できません。Coverage Auditで検証してください。')
        self.viz_export_note.setWordWrap(True)
        viz_layout.addWidget(self.viz_export_note)
        self.viz_bar_status = QLabel('表示ONの温度レイヤを自動検出します。')
        self.viz_bar_status.setWordWrap(True)
        viz_layout.addWidget(self.viz_bar_status)
        notice = QLabel('表示色・凡例のみ変更。温度値、GeoPackage、解析式は変更しません。\n'
                        'SurfaceのGROUND/ROOF/WALLは同一時刻・同一フォルダ内だけで共通スケールです。')
        notice.setWordWrap(True)
        viz_layout.addWidget(notice)
        lay.addWidget(viz_group)
        self._viz_sets = []
        self.refresh_visualization()

        # Phase 3: query *existing* GIS patch and MRT point geometries.
        # Selecting an explicit run prevents mixing different model exports.
        point_group = QGroupBox('Phase 3｜Spatial Evaluation：点評価（BETA）')
        point_lay = QVBoxLayout(point_group)
        intro_point = QLabel('地図を1点クリックして、GROUND/ROOFの該当パッチ温度と、'
                             '近傍MRT評価点を確認します。補間や再計算はしません。')
        intro_point.setWordWrap(True)
        point_lay.addWidget(intro_point)
        point_lay.addWidget(QLabel('解析データ（同じ解析親フォルダ・日時のみ自動対応）'))
        self.point_target = QComboBox()
        self.point_target.setMinimumWidth(270)
        point_lay.addWidget(self.point_target)
        row = QHBoxLayout()
        row.addWidget(QLabel('MRT探索半径'))
        self.point_radius = QDoubleSpinBox()
        self.point_radius.setRange(0.5, 50.0)
        self.point_radius.setDecimals(1)
        self.point_radius.setSingleStep(1.0)
        self.point_radius.setSuffix(' m')
        self.point_radius.setValue(10.0)
        row.addWidget(self.point_radius)
        row.addStretch()
        point_lay.addLayout(row)
        buttons = QHBoxLayout()
        self.point_start = QPushButton('地図クリックで点評価')
        self.point_start.clicked.connect(self.handler.start_point_evaluation)
        buttons.addWidget(self.point_start)
        self.point_stop = QPushButton('クリック終了')
        self.point_stop.clicked.connect(self.handler.stop_point_evaluation)
        self.point_stop.setEnabled(False)
        buttons.addWidget(self.point_stop)
        point_lay.addLayout(buttons)
        self.point_status = QLabel('解析レイヤを確認してからクリック評価を開始してください。')
        self.point_status.setWordWrap(True)
        point_lay.addWidget(self.point_status)
        self.point_result = QPlainTextEdit()
        self.point_result.setReadOnly(True)
        self.point_result.setMinimumHeight(190)
        self.point_result.setPlaceholderText('地図をクリックすると、温度と採用したパッチ／評価点を表示します。')
        point_lay.addWidget(self.point_result)
        self.point_copy = QPushButton('点評価結果をコピー')
        self.point_copy.clicked.connect(self.copy_point_result)
        point_lay.addWidget(self.point_copy)
        note_point = QLabel('GROUND/ROOF：クリック位置を含むポリゴンをそのまま参照。'
                            'WALL：2 m以内の重心点。MRT：指定半径内の最近傍値と周辺点統計。'
                            'レイヤの表示ON/OFFに関係なく、選択したデータを読み取ります。'
                            'HIPの点評価はこの版では対象外です。')
        note_point.setWordWrap(True)
        point_lay.addWidget(note_point)
        lay.addWidget(point_group)
        self._point_sets = []
        self.refresh_point_datasets()

        # Phase 3.1: line evaluation from already calculated MRT points.
        route_group = QGroupBox('Phase 3｜線評価：MRT経路プロファイル（BETA）')
        route_lay = QVBoxLayout(route_group)
        intro_route = QLabel('地図上で経路の折れ点を順にクリックしてから「経路を確定して評価」。'
                             'MRT評価点の最近傍値を距離に沿って示します。欠測は線をつなぎません。')
        intro_route.setWordWrap(True)
        route_lay.addWidget(intro_route)
        route_lay.addWidget(QLabel('解析データ（MRTレイヤを含む実行結果）'))
        self.route_target = QComboBox()
        route_lay.addWidget(self.route_target)
        settings_row = QHBoxLayout()
        self.route_step = QDoubleSpinBox()
        self.route_step.setRange(2.0, 100.0)
        self.route_step.setSingleStep(5.0)
        self.route_step.setValue(10.0)
        self.route_step.setSuffix(' m')
        self.route_radius = QDoubleSpinBox()
        self.route_radius.setRange(1.0, 100.0)
        self.route_radius.setSingleStep(5.0)
        self.route_radius.setValue(20.0)
        self.route_radius.setSuffix(' m')
        settings_row.addWidget(QLabel('間隔'))
        settings_row.addWidget(self.route_step)
        settings_row.addWidget(QLabel('探索半径'))
        settings_row.addWidget(self.route_radius)
        route_lay.addLayout(settings_row)
        route_buttons = QHBoxLayout()
        self.route_start = QPushButton('経路作図を開始')
        self.route_start.clicked.connect(self.handler.start_route_evaluation)
        route_buttons.addWidget(self.route_start)
        self.route_undo = QPushButton('1点戻す')
        self.route_undo.clicked.connect(self.handler.undo_route_vertex)
        self.route_undo.setEnabled(False)
        route_buttons.addWidget(self.route_undo)
        route_lay.addLayout(route_buttons)
        route_buttons2 = QHBoxLayout()
        self.route_finish = QPushButton('経路を確定して評価')
        self.route_finish.clicked.connect(self.handler.finish_route_evaluation)
        self.route_finish.setEnabled(False)
        route_buttons2.addWidget(self.route_finish)
        self.route_cancel = QPushButton('作図中止')
        self.route_cancel.clicked.connect(self.handler.cancel_route_evaluation)
        self.route_cancel.setEnabled(False)
        route_buttons2.addWidget(self.route_cancel)
        route_lay.addLayout(route_buttons2)
        self.route_status = QLabel('MRTレイヤのあるQGZを開いてから、経路を作図してください。')
        self.route_status.setWordWrap(True)
        route_lay.addWidget(self.route_status)
        self.route_plot = MrtRoutePlot()
        route_lay.addWidget(self.route_plot)
        self.route_result = QPlainTextEdit()
        self.route_result.setReadOnly(True)
        self.route_result.setMinimumHeight(180)
        route_lay.addWidget(self.route_result)
        self.route_export = QPushButton('距離プロファイルをCSV保存…')
        self.route_export.setEnabled(False)
        self.route_export.clicked.connect(self.handler.export_route_csv)
        route_lay.addWidget(self.route_export)
        explanation = QLabel('CSVには経路距離・投影座標・最近傍MRT・評価点ID・探索距離を記録。'
                             '未検出区間の温度は空欄です。距離重み付きの代表値はサンプリング近似で、'
                             '実際の連続温度・歩行暴露量を意味しません。'
                             '元データは変更しません。')
        explanation.setWordWrap(True)
        route_lay.addWidget(explanation)
        lay.addWidget(route_group)
        self._route_sets = []
        self.refresh_route_datasets()

        self.handler.area.build(self, lay)
        self.handler.comparison.build(self, lay)

        footer = QGroupBox('4｜実行結果')
        foot_lay = QVBoxLayout(footer)
        self.status = QLabel('入力manifestを指定して、処理ボタンを押してください。')
        self.status.setWordWrap(True)
        foot_lay.addWidget(self.status)
        self.last_path = QLineEdit()
        self.last_path.setReadOnly(True)
        self.last_path.setPlaceholderText('最後の出力フォルダ')
        foot_lay.addWidget(self.last_path)
        row = QHBoxLayout()
        folder_btn = QPushButton('出力フォルダを開く')
        folder_btn.clicked.connect(self.open_folder)
        row.addWidget(folder_btn)
        report_btn = QPushButton('診断レポートを開く')
        report_btn.clicked.connect(self.open_report)
        row.addWidget(report_btn)
        foot_lay.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(250)
        self.log.setMinimumHeight(120)
        self.log.setPlaceholderText('実行結果やエラーがここに表示されます。')
        foot_lay.addWidget(self.log)
        lay.addWidget(footer)
        note = QLabel('注意：HIP・地域集計は引き続き PROVISIONAL。\n'
                      'Coverage Auditは穴を補完しません。入力ファイルは変更しません。')
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch()
        scroll.setWidget(host)
        self.setWidget(scroll)

    def _viz_follow_changed(self, enabled):
        self.viz_target.setEnabled(not enabled)
        self.handler.update_map_colorbar()

    def _viz_auto_changed(self, enabled):
        self.viz_min.setEnabled(not enabled)
        self.viz_max.setEnabled(not enabled)
        self._viz_target_changed()

    def _viz_target_changed(self, *_args):
        idx = self.viz_target.currentIndex()
        if idx < 0 or idx >= len(self._viz_sets):
            self.viz_apply.setEnabled(False)
            self.handler.update_map_colorbar()
            return
        self.viz_apply.setEnabled(True)
        kind, _folder, _stamp, layers = self._viz_sets[idx]
        try:
            low, high = visualization.data_range(
                layers, 'surface_c' if kind == 'surface' else 'mrt_c')
            if self.viz_auto.isChecked():
                self.viz_min.setValue(round(low, 1))
                self.viz_max.setValue(round(high, 1))
            view_low, view_high = ((low, high) if self.viz_auto.isChecked() else
                                   visualization.valid_range(self.viz_min.value(), self.viz_max.value()))
            cuts = visualization.breaks(view_low, view_high, visualization.SURFACE_COLORS
                                         if kind == 'surface' else visualization.MRT_COLORS)
            self.viz_legend.setText(visualization.legend_html(cuts, kind))
        except Exception as e:
            self.viz_legend.setText('レイヤ情報を取得できません: ' + str(e))
        # The map colorbar never guesses from unapplied spinbox values.
        self.handler.update_map_colorbar()

    def refresh_visualization(self, *_args, select_kind=None):
        project = QgsProject.instance()
        grouped = {}
        for layer in project.mapLayers().values():
            parsed = visualization.parse_campus3d_temperature_layer(layer)
            if not parsed:
                continue
            kind, folder, filename = parsed
            try:
                stamp = str(next(layer.getFeatures())['time_jst'])
            except (StopIteration, KeyError, IndexError, TypeError):
                continue
            key = visualization.dataset_key(kind, folder, stamp)
            grouped.setdefault(key, []).append(layer)
        self._viz_sets = [(*key, layers) for key, layers in sorted(grouped.items())]
        old_text = self.viz_target.currentText()
        self.viz_target.blockSignals(True)
        self.viz_target.clear()
        index_to_select = -1
        for idx, (kind, folder, stamp, layers) in enumerate(self._viz_sets):
            title = ('Surface' if kind == 'surface' else 'MRT')
            label = '%s  %s  [%s]  (%s)' % (title, stamp, os.path.basename(folder), len(layers))
            self.viz_target.addItem(label)
            if label == old_text or (select_kind and kind == select_kind):
                index_to_select = idx
        if index_to_select >= 0:
            self.viz_target.setCurrentIndex(index_to_select)
        self.viz_target.blockSignals(False)
        if not self._viz_sets:
            self.viz_legend.setText('MRT／SurfaceのGeoPackageがまだ読み込まれていません。')
        self._viz_target_changed()
        if hasattr(self, 'point_target'):
            self.refresh_point_datasets()
        if self.handler.comparison.dock is not None:
            self.handler.comparison.refresh()

    def refresh_point_datasets(self):
        if not hasattr(self, 'point_target'):
            return
        previous_key = self.point_target.currentData()
        self._point_sets = build_point_datasets(self._viz_sets)
        self.point_target.blockSignals(True)
        self.point_target.clear()
        selected = 0
        for i, (key, stamp, records, ambiguous) in enumerate(self._point_sets):
            folder = key[1][0]
            kinds = {entry[0] for entry in records}
            label = ('Surface＋MRT' if kinds == {'surface', 'mrt'} else
                     'Surfaceのみ' if 'surface' in kinds else 'MRTのみ')
            if ambiguous:
                label += '【同時刻の複数出力につき独立】'
            parent_label = folder.replace('\\', '/').rstrip('/')
            label = '%s  [%s]  %s' % (stamp, label, parent_label[-46:])
            self.point_target.addItem(label, key)
            self.point_target.setItemData(i, folder, 3)  # ToolTipRole
            if key == previous_key:
                selected = i
        if self._point_sets:
            self.point_target.setCurrentIndex(selected)
        self.point_target.blockSignals(False)
        self.point_start.setEnabled(bool(self._point_sets))
        if not self._point_sets:
            self.point_status.setText('MRTまたはSurfaceの既存GPKGがありません。'
                                      'Phase 2で用いたQGZを開いてください。')
        if hasattr(self, 'route_target'):
            self.refresh_route_datasets()
        if self.handler.area.dock is not None:
            self.handler.area.refresh()

    def refresh_route_datasets(self):
        if not hasattr(self, 'route_target'):
            return
        previous_key = self.route_target.currentData()
        # Never accept a surface-only dataset for an MRT route profile.
        self._route_sets = [record for record in self._point_sets
                            if any(entry[0] == 'mrt' and len(entry[3]) == 1
                                   for entry in record[2])]
        self.route_target.blockSignals(True)
        self.route_target.clear()
        selected = 0
        for i, (key, stamp, entries, ambiguous) in enumerate(self._route_sets):
            folder = key[1][0].replace('\\','/')
            label = '%s  [%s]  %s' % (stamp, 'MRT＋Surface' if len(entries)>1 else 'MRT', folder[-44:])
            if ambiguous:
                label += '【独立実行】'
            self.route_target.addItem(label, key)
            if key == previous_key:
                selected = i
        if self._route_sets:
            self.route_target.setCurrentIndex(selected)
        self.route_target.blockSignals(False)
        # Never re-enable start during an active drawing operation.
        if self.handler._route_tool is None:
            self.route_start.setEnabled(bool(self._route_sets))
        if not self._route_sets:
            self.route_status.setText('MRT評価点を含む既存GPKGがありません。'
                                      'MRTを読み込んだQGZを開いてください。')

    def copy_point_result(self):
        result = self.point_result.toPlainText().strip()
        if result:
            QApplication.clipboard().setText(result)
            self.point_status.setText('点評価結果をクリップボードにコピーしました。')

    def _add_button(self, layout, text, operation, helptext):
        b = QPushButton(text)
        b.setToolTip(helptext)
        b.clicked.connect(lambda checked=False, op=operation: self.handler.run(op))
        layout.addWidget(b)
        self.buttons.append(b)
        description = QLabel(helptext)
        description.setWordWrap(True)
        description.setStyleSheet('color: gray; font-size: 10px;')
        layout.addWidget(description)

    def choose(self, kind):
        start = self.inputs[kind].text().strip()
        if not os.path.exists(start):
            start = os.path.dirname(start) if os.path.isdir(os.path.dirname(start)) else os.path.expanduser('~')
        path, _ = QFileDialog.getOpenFileName(self, 'Campus3D — ' + INPUT_LABELS[kind],
                                             start, 'JSON (*.json)')
        if path:
            self.set_path(kind, path)
        return path

    def set_path(self, kind, path):
        self.inputs[kind].setText(path)
        self.settings.setValue('Campus3DGISFeedback/v050/' + kind, path)

    def select_path(self, kind):
        path = self.inputs[kind].text().strip()
        if not path or not os.path.isfile(path):
            path = self.choose(kind)
        return path

    def set_busy(self, busy):
        for button in self.buttons:
            button.setEnabled(not busy)
        if busy:
            self.viz_apply.setEnabled(False)
        else:
            self._viz_target_changed()

    def write(self, message):
        self.log.appendPlainText(message)

    def record_output(self, folder, report=None):
        self.last_folder = folder
        self.last_report = report if report and os.path.isfile(report) else None
        self.last_path.setText(folder)
        self.last_path.setToolTip(folder)
        self.status.setText('完了：' + os.path.basename(folder))
        self.write('出力先：' + folder)
        if self.last_report:
            self.write('レポート：' + self.last_report)

    def open_folder(self):
        if self.last_folder and os.path.isdir(self.last_folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_folder))
        else:
            QMessageBox.information(self, 'Campus3D', '出力フォルダがまだありません。')

    def open_report(self):
        if self.last_report and os.path.isfile(self.last_report):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_report))
        else:
            QMessageBox.information(self, 'Campus3D', 'レポートはまだありません。')


class Campus3DGISFeedbackPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None
        self.running = False
        self.map_colorbar = None
        self._watch_root = None
        self._refresh_pending = False
        self._known_project_layer_ids = frozenset()
        self._point_tool = None
        self._previous_point_tool = None
        self._point_marker = None
        self._route_tool = None
        self._previous_route_tool = None
        self._route_vertices = []
        self._route_band = None
        self._route_rows = []
        self._route_stamp = None
        self._route_crs = None
        self.area = AreaPanel(self)
        self.comparison = ComparisonPanel(self)

    def initGui(self):
        self.action = QAction('Campus3D GIS Feedback', self.iface.mainWindow())
        self.action.setObjectName('Campus3DGISFeedbackOpenPanel')
        self.action.triggered.connect(self.show_panel)
        self.iface.addPluginToMenu('Campus3D GIS Feedback', self.action)
        self.iface.addToolBarIcon(self.action)
        self.map_colorbar = MapTemperatureColorbar(self.iface.mapCanvas())
        self.dock = Campus3DFeedbackDock(self.iface.mainWindow(), self)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()
        self._known_project_layer_ids = frozenset(QgsProject.instance().mapLayers())
        self._ensure_root_watched()
        # QGIS canvas also signals reordering and changes to map themes.
        self.iface.mapCanvas().layersChanged.connect(self._queue_visibility_refresh)
        QgsProject.instance().layersAdded.connect(self._queue_visibility_refresh)
        QgsProject.instance().layersRemoved.connect(self._queue_visibility_refresh)
        self.update_map_colorbar()

    def _ensure_root_watched(self):
        root = QgsProject.instance().layerTreeRoot()
        if self._watch_root is root:
            return
        if self._watch_root is not None:
            try:
                self._watch_root.visibilityChanged.disconnect(self._queue_visibility_refresh)
            except (TypeError, RuntimeError):
                pass
        self._watch_root = root
        root.visibilityChanged.connect(self._queue_visibility_refresh)

    def _queue_visibility_refresh(self, *_args):
        if self._refresh_pending or self.dock is None:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._run_visibility_refresh)

    def _run_visibility_refresh(self):
        self._refresh_pending = False
        if self.dock is None:
            return
        self._ensure_root_watched()
        project_ids = frozenset(QgsProject.instance().mapLayers())
        if project_ids != self._known_project_layer_ids:
            self.comparison.spatial.invalidate()
            self.area.invalidate()
            if self._route_tool is not None:
                self.cancel_route_evaluation()
            # A reloaded/changed project must not retain a stale route overlay
            # or enable CSV exporting results from removed source layers.
            self._remove_route_band()
            self._route_rows = []
            if self.dock is not None:
                self.dock.route_plot.set_rows([])
                self.dock.route_export.setEnabled(False)
            self._known_project_layer_ids = project_ids
            # Reload datasets on QGZ project change without heavy reimport.
            self.dock.refresh_visualization()
        else:
            self.update_map_colorbar()

    def show_panel(self):
        if self.dock is not None:
            self.dock.show()
            self.dock.raise_()

    def start_point_evaluation(self, *_args):
        dock = self.dock
        if dock is None or not dock._point_sets:
            return
        if dock.point_target.currentIndex() < 0:
            QMessageBox.information(dock, 'Campus3D', '点評価に使う解析データを選択してください。')
            return
        if self._point_tool is not None:
            return
        self.comparison.spatial.cancel()
        self.area.cancel()
        if self._route_tool is not None:
            self.cancel_route_evaluation()
        canvas = self.iface.mapCanvas()
        self._previous_point_tool = canvas.mapTool()
        self._point_tool = QgsMapToolEmitPoint(canvas)
        self._point_tool.canvasClicked.connect(self._on_point_clicked)
        canvas.setMapTool(self._point_tool)
        dock.point_start.setEnabled(False)
        dock.point_stop.setEnabled(True)
        dock.point_status.setText('地図上の調べたい場所をクリックしてください。'
                                  '右側に該当パッチと近傍MRTを表示します。')

    def _on_point_clicked(self, point, mouse_button):
        if mouse_button != Qt.LeftButton or self.dock is None:
            return
        dock = self.dock
        index = dock.point_target.currentIndex()
        if index < 0 or index >= len(dock._point_sets):
            dock.point_status.setText('解析データがありません。対象を再選択してください。')
            return
        dataset = dock._point_sets[index][2]
        canvas = self.iface.mapCanvas()
        try:
            report = evaluate_point(point, canvas.mapSettings().destinationCrs(),
                                    dataset, dock.point_radius.value())
            dock.point_result.setPlainText(report)
            dock.point_status.setText('点評価できました。別の位置を続けてクリックできます。')
            if self._point_marker is None:
                self._point_marker = QgsVertexMarker(canvas)
                self._point_marker.setIconType(QgsVertexMarker.ICON_CROSS)
                self._point_marker.setIconSize(14)
                self._point_marker.setPenWidth(3)
                self._point_marker.setColor(QColor('#e62b27'))
            self._point_marker.setCenter(point)
        except Exception as exc:
            dock.point_status.setText('点評価エラー：' + str(exc))
            dock.point_result.setPlainText(type(exc).__name__ + ': ' + str(exc))
            dock.write('Phase 3点評価 ERROR: ' + traceback.format_exc())

    def stop_point_evaluation(self, *_args):
        if self._point_tool is None:
            return
        canvas = self.iface.mapCanvas()
        tool = self._point_tool
        self._point_tool = None
        try:
            tool.canvasClicked.disconnect(self._on_point_clicked)
        except (RuntimeError, TypeError):
            pass
        if canvas.mapTool() is tool:
            try:
                if self._previous_point_tool is not None and self._previous_point_tool is not tool:
                    canvas.setMapTool(self._previous_point_tool)
                else:
                    self.iface.actionPan().trigger()
            except RuntimeError:
                self.iface.actionPan().trigger()
        self._previous_point_tool = None
        tool.deleteLater()
        if self.dock is not None:
            self.dock.point_start.setEnabled(bool(self.dock._point_sets))
            self.dock.point_stop.setEnabled(False)
            self.dock.point_status.setText('クリック評価を終了しました。結果はそのまま残しています。')

    def _sync_route_controls(self):
        dock = self.dock
        if dock is None:
            return
        editing = self._route_tool is not None
        dock.route_start.setEnabled(bool(dock._route_sets) and not editing)
        dock.route_target.setEnabled(not editing)
        dock.route_step.setEnabled(not editing)
        dock.route_radius.setEnabled(not editing)
        dock.route_undo.setEnabled(editing and bool(self._route_vertices))
        dock.route_finish.setEnabled(editing and len(self._route_vertices) >= 2)
        dock.route_cancel.setEnabled(editing)

    def _draw_route(self):
        if self._route_band is None:
            return
        self._route_band.reset(QgsWkbTypes.LineGeometry)
        for x,y in self._route_vertices:
            self._route_band.addPoint(QgsPointXY(x,y), True)

    def _remove_route_band(self):
        band = self._route_band
        self._route_band = None
        if band is not None:
            try:
                self.iface.mapCanvas().scene().removeItem(band)
            except RuntimeError:
                pass

    def start_route_evaluation(self, *_args):
        dock = self.dock
        if dock is None or not dock._route_sets:
            return
        if self._route_tool is not None:
            return
        self.comparison.spatial.cancel()
        self.area.cancel()
        self.stop_point_evaluation()
        canvas = self.iface.mapCanvas()
        self._route_rows = []
        self._route_vertices = []
        self._route_stamp = None
        self._route_crs = None
        dock.route_plot.set_rows([])
        dock.route_result.clear()
        dock.route_export.setEnabled(False)
        self._remove_route_band()
        self._route_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._route_band.setColor(QColor('#de3d32'))
        self._route_band.setWidth(3)
        self._previous_route_tool = canvas.mapTool()
        self._route_tool = QgsMapToolEmitPoint(canvas)
        self._route_tool.canvasClicked.connect(self._on_route_clicked)
        canvas.setMapTool(self._route_tool)
        dock.route_status.setText('経路の折れ点を順番にクリックしてください。'
                                  '2点以上で「経路を確定して評価」を押します。')
        self._sync_route_controls()

    def _on_route_clicked(self, point, mouse_button):
        if self._route_tool is None or mouse_button != Qt.LeftButton:
            return
        if len(self._route_vertices) >= 200:
            self.dock.route_status.setText('頂点の上限200点です。経路を確定してください。')
            return
        self._route_vertices.append((float(point.x()),float(point.y())))
        self._draw_route()
        self.dock.route_status.setText('経路頂点：%d点。必要な場所を続けてクリックしてください。' %
                                       len(self._route_vertices))
        self._sync_route_controls()

    def undo_route_vertex(self, *_args):
        if self._route_tool is not None and self._route_vertices:
            self._route_vertices.pop()
            self._draw_route()
            self.dock.route_status.setText('頂点を1つ戻しました。残り%d点' % len(self._route_vertices))
            self._sync_route_controls()

    def _release_route_tool(self):
        tool = self._route_tool
        if tool is None:
            return
        self._route_tool = None
        canvas = self.iface.mapCanvas()
        try:
            tool.canvasClicked.disconnect(self._on_route_clicked)
        except (RuntimeError, TypeError):
            pass
        if canvas.mapTool() is tool:
            if self._previous_route_tool is not None and self._previous_route_tool is not tool:
                try:
                    canvas.setMapTool(self._previous_route_tool)
                except RuntimeError:
                    self.iface.actionPan().trigger()
            else:
                self.iface.actionPan().trigger()
        self._previous_route_tool = None
        tool.deleteLater()
        self._sync_route_controls()

    def cancel_route_evaluation(self, *_args):
        if self._route_tool is None:
            return
        self._release_route_tool()
        self._route_vertices = []
        self._remove_route_band()
        if self.dock is not None:
            self.dock.route_status.setText('経路作図を中止しました。前回の評価結果は残しています。')

    def finish_route_evaluation(self, *_args):
        dock = self.dock
        if dock is None or len(self._route_vertices) < 2 or self._route_tool is None:
            return
        index = dock.route_target.currentIndex()
        if index < 0 or index >= len(dock._route_sets):
            dock.route_status.setText('MRT解析データを選択してください。')
            return
        dataset = dock._route_sets[index][2]
        try:
            rows, stats, report, stamp, crs = evaluate_route(
                self._route_vertices,
                self.iface.mapCanvas().mapSettings().destinationCrs(),
                dataset, dock.route_step.value(), dock.route_radius.value())
            self._route_rows = rows
            self._route_stamp = stamp
            self._route_crs = crs
            dock.route_plot.set_rows(rows)
            dock.route_result.setPlainText(report)
            dock.route_export.setEnabled(True)
            self._release_route_tool()
            dock.route_status.setText('線評価完了：%d点中%d点有効（欠測%d点）。'
                                      '必要ならCSV保存できます。' % (
                                          stats['samples'],stats['valid_samples'],
                                          stats['missing_samples']))
        except Exception as exc:
            dock.route_status.setText('線評価エラー：' + str(exc))
            dock.write('Phase 3線評価 ERROR: ' + traceback.format_exc())
            # Keep the drawing tool active: the user may undo or adjust vertices.

    def export_route_csv(self, *_args):
        dock = self.dock
        if dock is None or not self._route_rows:
            return
        project = QgsProject.instance()
        directory = dock.settings.value('Campus3DGISFeedback/v061/route_csv_dir', '', type=str)
        if not os.path.isdir(directory):
            directory = os.path.dirname(project.fileName() or '')
        if not os.path.isdir(directory):
            directory = os.path.expanduser('~')
        default_name = 'Campus3D_MRT_Route_%s.csv' % datetime.now().strftime('%Y%m%d_%H%M%S')
        path, _ = QFileDialog.getSaveFileName(dock,'Campus3D — MRT経路プロファイル CSV',
                                              os.path.join(directory,default_name),'CSV (*.csv)')
        if not path:
            return
        if not path.lower().endswith('.csv'):
            path += '.csv'
        try:
            with open(path,'w',encoding='utf-8-sig',newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['time_jst','crs','distance_m','x_m','y_m',
                                 'mrt_c','nearest_point_id','nearest_distance_m','status'])
                for r in self._route_rows:
                    has = r['mrt_c'] is not None
                    writer.writerow([self._route_stamp,self._route_crs,
                                     '%.3f'%r['distance_m'],'%.3f'%r['x_m'],
                                     '%.3f'%r['y_m'],'%.4f'%r['mrt_c'] if has else '',
                                     r['point_id'] if has else '',
                                     '%.3f'%r['nearest_distance_m'] if has else '',
                                     'VALID' if has else 'NO_DATA'])
            dock.settings.setValue('Campus3DGISFeedback/v061/route_csv_dir',
                                   os.path.dirname(path))
            dock.route_status.setText('CSV保存：'+path)
            dock.write('Phase 3線評価CSV: '+path)
            self.iface.messageBar().pushSuccess('Campus3D GIS Feedback', '線評価CSVを保存しました。')
        except Exception as exc:
            QMessageBox.warning(dock,'Campus3D GIS Feedback',
                                'CSV保存エラー：'+str(exc))

    def unload(self):
        self.comparison.applied = None
        self.comparison.spatial.invalidate()
        self.comparison.spatial.dock = None
        self.comparison.dock = None
        self.area.invalidate()
        self.area.dock = None
        self.cancel_route_evaluation()
        self._remove_route_band()
        self.stop_point_evaluation()
        if self._point_marker is not None:
            try:
                self.iface.mapCanvas().scene().removeItem(self._point_marker)
            except RuntimeError:
                pass
            self._point_marker = None
        # Disconnect all signal listeners on plugin ZIP reinstall/reload.
        for sender, signal_name in (
            (self._watch_root, 'visibilityChanged'),
            (self.iface.mapCanvas(), 'layersChanged'),
            (QgsProject.instance(), 'layersAdded'),
            (QgsProject.instance(), 'layersRemoved'),
        ):
            if sender is None:
                continue
            try:
                getattr(sender, signal_name).disconnect(self._queue_visibility_refresh)
            except (AttributeError, RuntimeError, TypeError):
                pass
        self._watch_root = None
        try:
            checker = getattr(coverage_audit, '_campus3d_coverage_check', None)
            if checker is not None and self.iface.mapCanvas().mapTool() == checker.tool:
                self.iface.actionPan().trigger()
        except Exception:
            pass
        if self.map_colorbar is not None:
            self.map_colorbar.shutdown()
            self.map_colorbar = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu('Campus3D GIS Feedback', self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def _file_from_ui(self, key):
        value = self.dock.select_path(key)
        if not value:
            return None, None
        path, data = _safe_manifest(value, key)
        self.dock.set_path(key, path)
        return path, data

    def _organize_new_layers(self, new_ids):
        if not new_ids:
            return
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.findGroup(GROUP_NAME)
        if group is None:
            group = root.insertGroup(0, GROUP_NAME)
        for lid in new_ids:
            node = root.findLayer(lid)
            if node is None or node.parent() == group:
                continue
            new_node = node.clone()
            group.insertChildNode(0, new_node)
            node.parent().removeChildNode(node)
        group.setExpanded(True)

    def _reuse_existing(self, kind, data, folder):
        specs = _existing_gpkg_specs(kind, data, folder)
        if not specs:
            raise ValueError('GeoPackage仕様がありません。')
        exists = [os.path.isfile(p) for p, _, _, _ in specs]
        if not any(exists):
            return False
        if not all(exists):
            raise ValueError('GeoPackageが一部だけ存在します。元の出力フォルダを確認してください。上書きしません。')
        project = QgsProject.instance()
        existing = {_canonical_data_path(lyr.source()) for lyr in project.mapLayers().values()
                    if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and lyr.providerType() == 'ogr'}
        newlayers = []
        for path, table, name, count in specs:
            canonical = _canonical_data_path(path)
            if canonical in existing:
                continue
            layer = QgsVectorLayer(path + '|layername=' + table, name, 'ogr')
            if not layer.isValid():
                raise ValueError('GeoPackage読み込み失敗: ' + path)
            if layer.crs().authid() != data.get('crs'):
                raise ValueError('既存GeoPackageのCRSがmanifestと違います: ' + path)
            if layer.featureCount() != count:
                raise ValueError('既存GeoPackageの地物数がmanifestと違います: ' + path)
            required = ({'point_id','mrt_c','time_jst'} if kind == 'mrt' else
                        {'patch_id','surface_c','area_m2','time_jst'} if kind == 'surface' else
                        {'hip_c','status','time_jst'})
            if not required.issubset(set(layer.fields().names())):
                raise ValueError('既存GeoPackageの属性構成が想定と違います: ' + path)
            sample = next(layer.getFeatures(), None)
            if sample is None or str(sample['time_jst']) != str(data.get('timestamp_jst')):
                raise ValueError('既存GeoPackageの時刻がmanifestと違います: ' + path)
            newlayers.append(layer)
        # Existing Surface styling must use one shared min/max even if only
        # a subset of layers was missing from the current QGIS project.
        if kind == 'surface' and newlayers:
            all_layers = list(newlayers)
            seen_paths = {_canonical_data_path(lyr.source()) for lyr in all_layers}
            for lyr in project.mapLayers().values():
                if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and \
                   _canonical_data_path(lyr.source()) in {_canonical_data_path(spec[0]) for spec in specs} and \
                   _canonical_data_path(lyr.source()) not in seen_paths:
                    all_layers.append(lyr)
            _style_loaded_layers(kind, newlayers, data, references=all_layers)
        else:
            _style_loaded_layers(kind, newlayers, data)
        for layer in newlayers:
            project.addMapLayer(layer)
        self.dock.write('既存GeoPackageを再利用（再計算・上書きなし）: ' + str(len(newlayers)) + 'レイヤ追加。CRS・件数・基本属性・時刻を確認しました。数値の全面照合はCoverage Audit等で行ってください。')
        return True

    def apply_visualization(self):
        """Re-style an identified QGIS layer group. Does not write source files."""
        if self.dock is None or self.running:
            return
        idx = self.dock.viz_target.currentIndex()
        if idx < 0 or idx >= len(self.dock._viz_sets):
            QMessageBox.information(self.dock, 'Campus3D', '温度表示の対象レイヤがありません。')
            return
        kind, folder, stamp, layers = self.dock._viz_sets[idx]
        try:
            if self.dock.viz_auto.isChecked():
                low, high = visualization.data_range(
                    layers, 'surface_c' if kind == 'surface' else 'mrt_c')
            else:
                low, high = visualization.valid_range(
                    self.dock.viz_min.value(), self.dock.viz_max.value())
            cuts = visualization.apply_style(layers, kind, low, high)
            self.dock.viz_legend.setText(visualization.legend_html(cuts, kind))
            self.update_map_colorbar()
            self.dock.write('Phase 2表示設定: %s %s  %.1f～%.1f °C  %dレイヤ（GPKG無変更）' %
                            (kind, stamp, low, high, len(layers)))
            self.iface.mapCanvas().refresh()
            self.iface.messageBar().pushSuccess('Campus3D GIS Feedback',
                                               '温度表示を適用しました（入力データ変更なし）')
        except Exception as e:
            QMessageBox.warning(self.dock, 'Campus3D GIS Feedback', str(e))

    def _ordered_visible_layer_ids(self):
        """Canvas layers in visual order (top-first; QGIS 3.44 API).

        The canvas itself is authoritative: it already filters tree/group
        visibility and honors map themes, unlike unchecked project layers.
        """
        return [layer.id() for layer in self.iface.mapCanvas().layers(True)
                if layer is not None and layer.isValid()]

    def update_map_colorbar(self, *_args):
        """Sync legend with *visible* QGIS renderer, not unapplied UI limits.

        Automatic mode chooses the highest visible temperature layer on the
        canvas. Manual mode is available but cannot display a legend for a
        hidden temperature dataset. Neither mode changes layer visibility.
        """
        dock = self.dock
        bar = self.map_colorbar
        if dock is None or bar is None or not hasattr(dock, 'viz_map_colorbar'):
            return

        opts = dock.settings
        opts.setValue('Campus3DGISFeedback/v052/map_colorbar_visible',
                      dock.viz_map_colorbar.isChecked())
        if not hasattr(dock, 'viz_follow_visibility'):
            return
        auto = dock.viz_follow_visibility.isChecked()
        opts.setValue('Campus3DGISFeedback/v053/follow_visibility', auto)
        position = dock.viz_bar_position.currentData()
        size = dock.viz_bar_size.currentData()
        opts.setValue('Campus3DGISFeedback/v053/bar_position', position)
        opts.setValue('Campus3DGISFeedback/v053/bar_size', size)
        bar.configure(position, size)
        if hasattr(dock, 'viz_show_missing_note'):
            show_missing = dock.viz_show_missing_note.isChecked()
            opts.setValue('Campus3DGISFeedback/v054/show_missing_note', show_missing)
            bar.set_show_missing_note(show_missing)

        if not dock.viz_map_colorbar.isChecked():
            bar.clear()
            dock.viz_bar_status.setText('地図のカラーバーは非表示です。')
            return

        ordered_ids = self._ordered_visible_layer_ids()
        # A derived ΔMRT layer has its own sign-centred colorbar; never label it
        # as an absolute MRT/Surface temperature map.
        delta_legend = self.comparison.delta_legend(ordered_ids)
        if delta_legend is not None:
            cuts, subtitle = delta_legend
            bar.set_legend('delta_mrt', '', cuts, subtitle=subtitle)
            dock.viz_bar_status.setText('ΔMRT（B−A）｜0を中心に青＝低下、赤＝上昇')
            return
        group_ids = [[lyr.id() for lyr in record[3]] for record in dock._viz_sets]
        idx, count, message = select_dataset(
            group_ids, ordered_ids, dock.viz_target.currentIndex(), auto)
        if idx < 0:
            bar.clear()
            dock.viz_bar_status.setText(message)
            return

        # In automatic mode, keep the target controls and visible legend in
        # agreement.  Block the combo signal to avoid recursive updates.
        if auto and idx != dock.viz_target.currentIndex():
            dock.viz_target.blockSignals(True)
            dock.viz_target.setCurrentIndex(idx)
            dock.viz_target.blockSignals(False)
            # Update dock's temperature preview (does not apply any style).
            dock._viz_target_changed()
            # _viz_target_changed invokes this function once more, so return.
            return

        kind, _folder, stamp, layers = dock._viz_sets[idx]
        visible_ids = set(ordered_ids)
        visible_layers = [layer for layer in layers if layer.id() in visible_ids]
        cuts, warning = same_renderer_classes(
            visible_layers, 'surface_c' if kind == 'surface' else 'mrt_c')
        if warning:
            bar.clear()
            dock.viz_bar_status.setText(warning)
            return

        side = self.comparison.legend_context(ordered_ids, cuts)
        bar.set_legend(kind, stamp, cuts, prefix=side)
        label = 'Surface Temperature' if kind == 'surface' else 'MRT'
        dock.viz_bar_status.setText('%s｜%s %s' % (label, message,
                                    '(位置・サイズ変更可)'))

    def export_map_png(self, *_args):
        """One-click PNG of visible QGIS canvas + synchronized legend only."""
        if self.dock is None or self.map_colorbar is None:
            return
        self.update_map_colorbar()
        bar = self.map_colorbar
        if not bar.isVisible() or not bar._cuts:
            QMessageBox.information(self.dock, 'Campus3D GIS Feedback',
                                    '温度の凡例が表示されていません。\n'
                                    'SurfaceまたはMRTを表示ONにし、カラーバーもONにしてください。')
            return
        project = QgsProject.instance()
        project_name = os.path.splitext(os.path.basename(project.fileName() or 'Campus3D_Map'))[0]
        # Avoid forbidden path characters in Windows filenames.
        project_name = ''.join(c if c not in '<>:"/\\|?*' else '_' for c in project_name).strip() or 'Campus3D_Map'
        metric = 'DeltaMRT' if bar._kind == 'delta_mrt' else ('Surface' if bar._kind == 'surface' else 'MRT')
        if bar._prefix:
            metric += '_' + bar._prefix
        default_name = '%s_%s_%s.png' % (project_name, metric, datetime.now().strftime('%Y%m%d_%H%M%S'))
        last = self.dock.settings.value('Campus3DGISFeedback/v054/export_dir', '', type=str)
        directory = last if os.path.isdir(last) else os.path.dirname(project.fileName() or '')
        if not os.path.isdir(directory):
            directory = os.path.expanduser('~')
        destination, _ = QFileDialog.getSaveFileName(
            self.dock, 'Campus3D — 地図と温度カラーバーをPNG保存',
            os.path.join(directory, default_name), 'PNG画像 (*.png)')
        if not destination:
            return
        if not destination.lower().endswith('.png'):
            destination += '.png'
        try:
            width, height = bar.export_canvas_png(destination)
            self.dock.settings.setValue('Campus3DGISFeedback/v054/export_dir',
                                        os.path.dirname(destination))
            self.dock.write('Phase 2 PNG保存: %s (%d × %d px、表示中の地図＋温度凡例)' %
                            (destination, width, height))
            self.iface.messageBar().pushSuccess('Campus3D GIS Feedback',
                                                '凡例付きPNGを保存しました。')
        except Exception as exc:
            self.dock.write('PNG保存エラー: ' + type(exc).__name__ + ': ' + str(exc))
            QMessageBox.warning(self.dock, 'Campus3D GIS Feedback', str(exc))

    def _report_for(self, kind, folder):
        if kind == 'audit':
            name = 'coverage_audit_report.txt'
        elif kind == 'regional_calc':
            name = 'regional_hip_report.txt'
        elif kind == 'context':
            name = 'context_report.txt'
        else:
            name = 'export_report.txt'
        return os.path.join(folder, name)

    def run(self, operation):
        if self.running or self.dock is None:
            return
        kind = {'audit':'surface','regional_calc':'surface','context':'regional'}.get(operation,operation)
        manifest, meta = None, None
        try:
            manifest, meta = self._file_from_ui(kind)
            if not manifest:
                return
            self.running = True
            self.dock.set_busy(True)
            folder = os.path.dirname(manifest)
            self.dock.write('\n[' + datetime.now().strftime('%H:%M:%S') + '] ' + operation
                            + '\n入力: ' + manifest)
            previous_layers = set(QgsProject.instance().mapLayers())
            if operation in ('mrt','surface','hip'):
                if not self._reuse_existing(operation, meta, folder):
                    if operation == 'mrt':
                        import_mrt.run_import_mrt(manifest)
                    elif operation == 'surface':
                        import_surface.run_import_surface(manifest)
                    else:
                        import_hip.run_import_hip(manifest)
                output_folder = folder
            elif operation == 'audit':
                before = _subfolders(folder, 'Coverage_Audit_')
                coverage_audit.iface = self.iface
                coverage_audit.run_campus3d_coverage_audit(manifest)
                after = _subfolders(folder, 'Coverage_Audit_') - before
                if not after:
                    raise RuntimeError('Coverage Auditの新しい出力フォルダを確認できません。')
                output_folder = max(after, key=os.path.getmtime)
            elif operation == 'regional_calc':
                before = _subfolders(folder, 'Regional_HIP_')
                regional_hip.iface = self.iface
                regional_hip.run_regional_hip(manifest)
                after = _subfolders(folder, 'Regional_HIP_') - before
                if not after:
                    self.dock.write('処理がキャンセルされたか、出力が生成されませんでした。')
                    return
                output_folder = max(after, key=os.path.getmtime)
                regional_manifest = os.path.join(output_folder, 'regional_hip_manifest.json')
                if os.path.isfile(regional_manifest):
                    self.dock.set_path('regional', regional_manifest)
            elif operation == 'context':
                before = _subfolders(folder, 'Regional_Context_')
                regional_context.iface = self.iface
                regional_context.run_regional_context(manifest)
                after = _subfolders(folder, 'Regional_Context_') - before
                if not after:
                    self.dock.write('処理がキャンセルされたか、出力が生成されませんでした。')
                    return
                output_folder = max(after, key=os.path.getmtime)
            else:
                raise ValueError('Unknown tool: ' + operation)
            added = set(QgsProject.instance().mapLayers()) - previous_layers
            self._organize_new_layers(added)
            if operation in ('mrt', 'surface'):
                self.dock.refresh_visualization(select_kind=operation)
            report = self._report_for(operation, output_folder)
            self.dock.record_output(output_folder, report)
            self.iface.messageBar().pushSuccess('Campus3D GIS Feedback', '処理完了: ' + operation)
        except Exception as exc:
            short = type(exc).__name__ + ': ' + str(exc)
            self.dock.write('ERROR: ' + short)
            self.dock.write(traceback.format_exc())
            self.dock.status.setText('エラー：' + short)
            QMessageBox.critical(self.dock, 'Campus3D GIS Feedback v' + VERSION,
                                 short + '\n\n詳しい内容はパネル下部に表示しています。')
        finally:
            self.running = False
            if self.dock is not None:
                self.dock.set_busy(False)

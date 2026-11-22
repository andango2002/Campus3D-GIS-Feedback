# v0.7.2 Source mapping

v0.7.1 を基準として追加・変更したファイル：

- `comparison_spatial_core.py`（新規）: 純Pythonでの対応MRT点の空間検索、経路、区域集計。ID＋XY＋高さ照合は既存 `comparison_delta_core.py` に準拠。
- `comparison_spatial.py`（新規）: QGISジオメトリを用いた座標変換・Surfaceパッチの点・面比較と読み取り専用MRT比較アダプタ。
- `comparison_spatial_panel.py`（新規）: Phase 4専用の点／線／面作図UI、結果表示、CSV＋JSON保存。
- `route_plot_ab.py`（新規）: 同一温度縦軸上のA/Bルートグラフ。
- `comparison_panel.py`: 上記UIの組み込み、A/B選択変更時の結果無効化。
- `plugin.py`: 地図ツールの競合防止、プロジェクト変更やアンロード時の後始末、VERSION。
- `area_panel.py`: Phase 3面評価との地図ツール競合防止。
- `metadata.txt` / `__init__.py`: 版数更新。

既存の `engines/*`, `point_evaluation.py`, `route_profile.py`, `area_evaluation.py`, `comparison_delta_core.py`, `comparison_delta.py`, `map_colorbar.py` は無変更。

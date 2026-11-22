# Campus3D GIS Feedback v0.7.2 BETA — Release Notes

**タグ：** `v0.7.2-beta`  
**対象：** QGIS 3.40以降（同梱 `metadata.txt` に記載）  
**位置づけ：** 研究・教育向けの実験的なスナップショット。Stable版ではありません。  
**配布ZIP：** `Campus3D_GIS_Feedback_v0.7.2-beta_QGIS_Plugin.zip`

## 主な機能

- **GIS読み込み：** Blender/Post-Simが出力した `gis_manifest.json` に基づき、MRT、Surface Temperature、暫定HIPをQGISへ表示。
- **可視化：** MRT・Surface Temperatureの温度カラースケール、地図上の凡例、PNG出力、Surface Coverage Audit。
- **空間評価（Phase 3）：** 地図クリックによる点評価、経路に沿ったMRTプロファイル、区域内のMRT／GROUND／ROOFの統計。
- **比較評価（Phase 4）：** A/Bの共通温度スケール、ΔMRT（B−A）マップ、同一点・同経路・同区域のA/B統計、CSV／JSONレポート。

## インストール

1. 本リリースの **Assets** から `Campus3D_GIS_Feedback_v0.7.2-beta_QGIS_Plugin.zip` を取得。
2. QGISで **プラグイン → プラグインの管理とインストール → ZIPからインストール**。
3. ZIPを指定してインストール。既存の同名プラグイン更新後、必要ならQGISを再起動。
4. QGISの **Campus3D GIS Feedback** パネルで、各種 `gis_manifest.json` を読み込む。

**注意：** GitHubの自動生成する `Source code (zip)` はQGISプラグイン用ZIPと同一ではありません。必ず **Assetsの配布ZIP** を使ってください。

## 既知の制限・注意

- HIPとCoverage Auditの評価には **PROVISIONAL（暫定）** の機能が含まれます。入力データの欠損を自動補完するものではありません。
- MRTのA/B値は原則として **共通ID・XY・高さが一致する評価点**で照合。対応点がなければ欠測のままとし、異なる格子を安易に引き算しません。
- **Surface Temperatureの線比較、WALLのA/B数値比較、Surfaceのパッチごとの差分マップは未実装**。
- **A/Bに異なる時刻**を使った差は時間変化であり、建物・樹木変更の因果効果ではありません。
- 元のv0.7.2同梱 `QA_REPORT.md` は配布時点のオフライン検査を記録しています。ユーザーによるQGIS実機試行の履歴はありますが、全機能の網羅的な回帰試験完了を意味しません。

## 今回の公開パッケージについて

既存のv0.7.2プラグインZIPを **バイト単位で変更せず**、GitHub公開用に名前を整理しました。EnvSim Suite本体、GSI2Campus3D、授業用Campus3D Toolsのコードは同梱していません。

**ライセンス:** 元のv0.7.2配布物には明示的なLICENSEファイルがありません。公開・再利用条件はリポジトリ所有者が決定し、第三者由来コードとの整合性を確認してください。ライセンス未指定のまま公開する場合、第三者には原則として明確な再利用許諾が与えられません。

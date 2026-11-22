# Campus3D GIS Feedback

QGISで、Campus3D EnvSim / Post-Simの熱環境解析結果を地図に戻して表示・評価・比較するための研究・教育向け実験的プラグインです。

**Snapshot:** v0.7.2 BETA (`v0.7.2-beta`)  
**QGIS:** 3.40+（プラグインのmetadata.txtによる）

## 機能

- MRT・Surface Temperature・暫定HIPを `gis_manifest.json` から読み込み
- 温度カラースケール、地図凡例、PNG出力、Surface Coverage Audit
- 点・経路・区域の温熱指標評価（Phase 3）
- 共通スケール、ΔMRT、および同一点・同経路・同区域のA/B比較（Phase 4）
- 比較結果のCSV／JSON保存

詳細は [リリースノート](docs/RELEASE_NOTES_JA.md) を参照してください。

## 一般利用者向けインストール

GitHubの **Releases → Assets** から `Campus3D_GIS_Feedback_v0.7.2-beta_QGIS_Plugin.zip` を取得し、QGISの **プラグイン → プラグインの管理とインストール → ZIPからインストール** を選択します。

リポジトリの「Code → Download ZIP」やGitHub自動生成の「Source code (zip)」は、プラグイン配布ZIPの代わりにはしないでください。

## ソース構成

```text
campus3d_gis_feedback/   # QGISプラグイン本体。ZIPの直下に置く必要がある
  __init__.py
  metadata.txt
  plugin.py
  engines/
  ...
docs/                    # 公開資料・課題・公開手順
```

本リポジトリには、サンプルの学生作品・個人情報・都市モデルの大容量データ・解析結果一式を含めません。

## 注意

本版はBETAです。MRT A/B同一点比較には共通ID・位置・高さの一致が必要です。暫定HIPやCoverage Auditには制約があり、A/B異時刻差は設計変更の因果効果ではありません。網羅的なQGIS実機回帰試験は未完了です。

## License / 公開許諾

オリジナルv0.7.2パッケージにはLICENSEファイルがありませんでした。公開者は第三者由来部分も確認したうえで、希望する再利用許諾を別途定めてください。現時点で、特定のオープンソースライセンスが付与されているとは表明していません。

**補足:** プラグインmetadata.txtの `homepage` は従来のEnvSimリポジトリを指しています。独立リポジトリの正式URLが決まったら、次回リリースで修正する項目です。既存の動作確認済みv0.7.2を保護するため、今回のプラグインソースには変更を加えていません。

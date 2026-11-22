# GIS Feedback v0.7.2 BETA — 既知課題・公開範囲

## 公開できる機能

- MRT/Surface Temperatureの読み込み・温度凡例・PNG出力
- Phase 3: 点・経路・区域の評価
- Phase 4: MRT A/B共通スケール、ΔMRT、同一点・同経路・同区域比較
- Surface Temperature A/Bの点・区域比較
- CSV/JSON出力、暫定HIP、Coverage Audit

## 未完了または注意が必要

- 暫定HIPとCoverage AuditはPROVISIONAL
- Surfaceの経路比較、WALL A/B数値比較、Surfaceパッチ差分マップは未実装
- MRT共通点照合と、元の解析時刻・気象条件・ジオメトリの整合性に注意
- 大区域でのSurface集計処理時間・実機回帰テストは未検証
- 出力元のGPKG等のデータ欠損は本プラグインで自動補完しない
- `metadata.txt`の既存EnvSim homepageは新リポジトリ作成後に更新が必要
- 元アーカイブにLICENSEファイルなし。第三者由来ソースの権利確認とライセンス選定が必要

## 動作確認の範囲

オリジナルパッケージの`QA_REPORT.md`はオフラインの検査記録。QGIS実機のユーザー試験ではv0.7.2のA/B点・線・面表示が確認されたが、網羅的な回帰試験ではない。

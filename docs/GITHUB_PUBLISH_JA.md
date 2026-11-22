# Campus3D GIS Feedback v0.7.2 BETA — GitHub新規公開手順

## 先に用意するファイル

- `Campus3D_GIS_Feedback_v0.7.2-beta_GitHub_Source.zip`（GitHubに登録するソースツリー）
- `Campus3D_GIS_Feedback_v0.7.2-beta_QGIS_Plugin.zip`（利用者向けZIP。**Releases → Assets**へ添付）
- `Campus3D_GIS_Feedback_v0.7.2-beta_Release_Notes.md`（Release本文）

## 方法：GitHub Desktopで新しいリポジトリを作成

1. **GitHub Desktop** を起動してサインイン。`File → New repository` をクリック。
2. リポジトリ名を **`Campus3D-GIS-Feedback`** とし、保存場所を決めて `Create repository`。既存のEnvSimリポジトリとは分ける。
3. GitHub Desktopから `Repository → Show in Explorer` 等でローカルの作業フォルダを開く。
4. ソース管理用ZIPを展開し、**中身**（`campus3d_gis_feedback/`、`docs/`、`README.md` など）を作業フォルダ直下にコピーする。二重フォルダにしない。
5. GitHub Desktopの`Changes`で変更を確認。Summaryへ **`Initial beta snapshot: GIS Feedback v0.7.2`** と入力、`Commit to main`。
6. `Publish repository`を押し、公開するなら **`Keep this code private` のチェックを外す**。名称を確認してPublish。
7. ブラウザーで新しくできたGitHubリポジトリを開く。`campus3d_gis_feedback/metadata.txt`と`README.md`が見えるか確認。
8. `Releases → Draft a new release` へ進み、Tagを **`v0.7.2-beta`**（新規タグはmainに付ける）、Release titleを **`Campus3D GIS Feedback v0.7.2 BETA`** とする。
9. 本文へリリースノートの内容を貼る。**Set as a pre-release** をON。
10. Assets欄へ **利用者向けプラグインZIP** を添付し、`Publish release`。
11. リリース画面でAssetsからZIPを入手し、QGISの「ZIPからインストール」で再現できるか確認。新しいリポジトリのリンクが有効かも確認。

### Webブラウザーで先にリポジトリを作った場合

GitHubで `New repository` → `Campus3D-GIS-Feedback` → `Public` → `Create repository`。
その後GitHub Desktopで `File → Clone repository` を使って空のリポジトリを複製し、手順4から進めます。

## 公開時の注意

- **ZIPの役割を混同しないこと。** ソース用ZIPは展開してGitで管理し、QGISインストール用ZIPはReleasesのAssetsとして配布。
- `Source code (zip)` はGitHubが自動生成するため、本プラグインZIPと同一ではありません。
- **ライセンス：** 元プラグインにLICENSEなし。公開者が第三者由来部分を含む権利関係を確認して選定。未選定なら他人の再利用条件は明確になりません。
- **メタデータの旧リンク：** `campus3d_gis_feedback/metadata.txt` の `homepage` はEnvSim旧リポジトリ。新リポジトリのURL決定後、更新して将来版で配布する。
- プラグインと独立の **GSI2Campus3D／EnvSim Suite／授業用Campus3D Tools** は今回変更しない。
- 学生作品、`.blend`、`.qgz`、解析GPKG、CSV、個人のOneDriveパス、トークン、秘密情報は含めない。
- GitHub上での公開はQGIS公式プラグインリポジトリへの掲載と別手続き。

## 公式案内

- GitHub Desktop: https://docs.github.com/ja/desktop/overview/creating-your-first-repository-using-github-desktop
- GitHub Releases: https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository
- QGIS 3.44プラグイン管理: https://docs.qgis.org/3.44/ja/docs/user_manual/plugins/plugins.html

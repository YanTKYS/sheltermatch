# 第三者の著作物・データについて（ライセンスの適用範囲）

sheltermatch の自作部分は [MIT License](LICENSE)（Copyright (c) 2026 YanTKYS）で提供します。
この文書は、**MIT License を適用するものと、適用しないもの**を区別し、外部のデータ・ライブラリの
権利・利用条件の確認状況をまとめたものです。第三者の著作物・データには、各提供元の元のライセンス・
著作権表示・利用条件が引き続き適用されます。

## 1. MIT License の対象（自作部分）

| 対象 | 例 |
| --- | --- |
| Notebook | `sheltermatch.ipynb`、`address_geocode.ipynb`、`experiments/` の自作Notebook |
| Pythonモジュール・スクリプト | `src/**/*.py`、`experiments/**/*.py` |
| 画面 | `src/review/review_template.html`（HTML / CSS / JavaScript） |
| 設定・テンプレート | `configs/`、`templates/residents.csv`（値は公開情報に基づく設定値・見出し） |
| テスト・架空データ | `test/` の各テストと `fake_data.py`、`experiments/performance/` の架空の要支援者CSV |
| 文書 | `README.md`、`CHANGELOG.md`、`docs/**/*.md`、各フォルダの `README.md` |

実際の要支援者データ・実際の住所データは、このリポジトリに含めておらず、コミットもしません（個人情報のため、
MIT License の対象にも含みません）。

## 2. MIT License を適用しないもの

### 2.1 このリポジトリ内で、権利の確認が済んでいないため対象外としているもの

次のものは、第三者の画像・データが含まれる、または出どころが外部データのため、**確認が済むまでMIT Licenseの
対象外**とします（削除や内容の変更はしていません）。

| ファイル | 理由 |
| --- | --- |
| `docs/images/**`（レビュー画面の改訂前後のスクリーンショット） | 国土地理院の地理院タイル（背景地図）と、国・県等のハザード区域データを描画した画像を含むため。必要な出典表示・利用条件は未確認 |
| `experiments/address_geocoding/address_conversion_test_input.csv` | デジタル庁ABR（アドレス・ベース・レジストリ）の実データで照合した住所の文字列を含むため |
| `test/assigned_shelters.csv`、`test/residents_sample_enriched.csv` | 要支援者は架空だが、避難所の名称は BODIK の指定緊急避難場所データ（糸満市。CC BY 4.0）に由来するため。出典表示の整理が済むまで対象外 |
| `experiments/gsi_shelter_compare/gsi-bodik-comparison-20261003.md` | 比較結果の件数の記録。自作の文章だが、BODIK・国土地理院のデータの比較から得た数値のため、提供元の条件の確認が済むまで対象外 |

これらの権利の確認が済んだ後に、このファイルの表を更新します。

### 2.2 外部のデータ（リポジトリには含めない）

データ本体は、リポジトリに含めておらず、含めません（Notebook の実行時に、利用者が公式の配布元から取得します）。
**一律に MIT License や CC BY などを付けません。** 提供元・データセットごとの利用条件と出典表示に従ってください。

| データ | 使う場所 | 公式の確認先 | 確認状況 |
| --- | --- | --- | --- |
| BODIK 自治体標準オープンデータ（指定緊急避難場所）**糸満市**（既定の設定 `itoman-city` の resource_id `3132a0a4-f522-4b2d-bf18-f106d8b3a5ae`） | 避難所の名称・緯度・経度（BODIK Data API） | <https://data.bodik.jp/dataset/472107_evacuation_space> | **確認済み**: 提供 糸満市（情報政策課）、ライセンス「クリエイティブ・コモンズ 表示 4.0 国際」（[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)）。出典・ライセンスは、このデータをBODIK APIから取得したときの `sheltermatch_review.zip` の `THIRD_PARTY_NOTICES.txt` に載せる（下記の「避難所データの出典の付け方」） |
| BODIK の他の自治体のデータ・利用者が持ち込む避難所一覧CSV | 同上 | データセット・提供元ごとに確認 | **未確認**（上の糸満市の出典・ライセンスは付けない） |
| 国土数値情報 洪水浸水想定区域データ（A31a） | ハザード区域（洪水） | <https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A31.html>（国土数値情報の利用約款） | **未確認**（下記の注） |
| 国土数値情報 土砂災害警戒区域データ（A33） | ハザード区域（土砂災害） | <https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A33.html>（国土数値情報の利用約款） | **未確認**（下記の注） |
| 沖縄県 津波浸水想定図 | ハザード区域（津波） | <http://gis.pref.okinawa.jp/OpenData/> | **未確認**（下記の注） |
| 沖縄県 高潮浸水想定区域図 | ハザード区域（高潮） | <https://www.pref.okinawa.jp/machizukuri/kaigankasen/1013605/1040838.html> | **未確認**（下記の注） |
| デジタル庁 アドレス・ベース・レジストリ（ABR） | 住所→座標変換（`address_geocode.ipynb`） | <https://dataset.address-br.digital.go.jp/> | **未確認**（下記の注） |
| 国土地理院 地理院タイル（淡色地図） | レビューZIPの背景地図（画像にして同梱） | <https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html> | 利用条件の本文は、この作業では未確認（PRのレビューで公式ページの存在を確認）。出典「国土地理院」の表示に加え、**タイルを結合・切り出して画像に加工した旨**を、ZIP内の `gsi-basemap-NOTICE.txt`・`THIRD_PARTY_NOTICES.txt`・画面の出典表示で維持 |

#### 避難所データの出典の付け方

`sheltermatch.ipynb` は、避難所一覧の取得方法を記録し、`sheltermatch_review.zip` の `THIRD_PARTY_NOTICES.txt` を次のように分けて作ります。

| 避難所一覧の取得方法 | ZIPの通知 |
| --- | --- |
| BODIK APIから、出典を確認済みのデータ（上の糸満市）を取得できた | 出典・データセットのリンク・CC BY 4.0・全文リンクを表示。名称・緯度・経度の列だけを取り出し、要支援者との直線距離を計算して候補を並べた（加工した）旨と、糸満市が作成・保証したものではない旨を明示 |
| BODIK APIから、出典を確認していないデータセットを取得した | 取得したデータセットの出典・ライセンスをBODIKの公式ページで確認する案内だけ |
| CSVのアップロード（指定した場合・API取得に失敗して切り替わった場合） | 利用者が用意したCSVの提供元で、出典・利用条件を確認する案内だけ（糸満市の出典は付けない） |
| 記録なし | 同上（出どころ不明として、特定のデータの出典は付けない） |

出典を確認済みのデータを増やすときは、`src/review/review_builder.py` の `KNOWN_SHELTER_DATASETS` に、公式ページで確認した出典・ライセンスを追加します。

> **注（確認状況）**: 今回の作業環境では、これらの公式サイトへ接続できず、利用条件の本文を読めませんでした。
> そのため、**条件を推測で記載していません。** 実際に使うデータセットの利用条件（ライセンス表示の有無、出典の表記方法、
> 加工・再配布の条件）は、上の公式の確認先で、取得した時点のものを確認してください。確認できない場合は、
> 個別に提供元へ確認する前提のままにします。
> ハザード区域の画像（`sheltermatch_review.zip` の `assets/hazard_*.png`）や、背景地図の画像を再配布・
> 公開する場合は、提供元ごとに必要な出典表示と加工の注記を付けてください。

### 2.3 ソフトウェア

| 対象 | 扱い |
| --- | --- |
| Leaflet 1.9.4（`sheltermatch_review.zip` に同梱） | BSD 2-Clause License。再配布の際に必要な著作権表示・条件・免責条項を含む公式 `LICENSE` 本文を、ZIP内の `assets/leaflet-LICENSE.txt` として従来どおり同梱（バージョンとSHA-256を固定して取得） |
| Python / Google Colab の依存ライブラリ（numpy・pandas・geopandas・shapely・geopy・matplotlib・requests・pillow など） | このリポジトリでは再配布していません。Colab や、`test/requirements.txt` のとおり利用者の環境へ別途インストールされるだけで、MIT License の対象外です。各ライブラリのライセンスに従います |

## 3. 生成物の扱い

| 生成物 | 扱い |
| --- | --- |
| `assigned_shelters.csv` | 要支援者の住所・座標等の個人情報を含み得ます。MIT License の対象外です |
| `sheltermatch_review.zip` | 自作の画面部分（`review.html` の HTML / CSS / JavaScript）だけが MIT License の対象です。ZIP内の `LICENSE.txt`（MIT License全文）と `THIRD_PARTY_NOTICES.txt` で区別を示します。地図ライブラリ・背景地図・ハザード画像・埋め込まれた要支援者や避難所のデータは対象外で、Leaflet の `LICENSE` と背景地図の `NOTICE` も従来どおり同梱します |

## 4. 権利者・帰属の確認

著作権表示は、リポジトリのオーナーである `YanTKYS` の名義（2026年）にしています。リポジトリの履歴は、
`YanTKYS` と、AIコーディングアシスタント（Claude）による変更から成ります。**開発が所属組織（自治体等）の
業務として行われた場合は、成果物の権利が組織に帰属することがあります。** この点は、オーナーが所属組織の
規程に照らして確認した上で、この表示を使ってください（本リポジトリの記述だけでは、確認できていません）。

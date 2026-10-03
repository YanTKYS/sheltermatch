# test ディレクトリについて

`test/` には、架空の要支援者CSVや実行結果CSVなど、個人情報を含まないテスト用データのみを置く。

国・県等から取得した公式GISデータ本体(GeoJSON・Shapefile ZIP等)は、このリポジトリへコミットしない。

## 実機での回帰確認に使用した公式データ

以下の公式配布ZIPをGoogle Colab上で実際に投入し、`sheltermatch.ipynb` のハザード判定処理が
最後まで正常に完走することを確認した(詳細は [docs/project-status.md](../docs/project-status.md) を参照)。

* `A33-25_47_GEOJSON.zip` (国土数値情報A33、土砂災害警戒区域データ)
* `level1_1cm-30cm.zip` (沖縄県津波浸水想定)
* `47007_takasiosinnsuisoutei_22itoman.zip` (沖縄県高潮浸水想定)
* `A31a-25_47_20_GEOJSON.zip` (国土数値情報A31a、洪水・その他の河川)
* `A31a-25_47_10_GEOJSON.zip` (国土数値情報A31a、洪水・洪水予報河川・水位周知河川)

これらのファイル自体はこのリポジトリには含めていない。同じ確認を行う場合は、各データの公式配布元から
別途取得すること。

## 自動テスト

```bash
python3 -m pip install -r test/requirements.txt   # 初回のみ
python3 -m unittest discover -s test
```

`main` 向けの Pull Request と `main` への push では、GitHub Actions（`.github/workflows/ci.yml`）でも
同じテストを実行し、結果を Pull Request の Checks で確認できる。CI では依存ライブラリが揃っていることを
確認したうえで、外部へ接続できない状態でテストを実行し、skip が1件でもあれば失敗とする。

実際の Google Colab での実行、公式ハザードデータ、実ブラウザでのレビュー画面の確認は CI の対象外で、従来どおり
実機確認として行う。すべてのテストは完全な架空データ（`fake_data.py`）だけを使い、外部通信を必要としない。

`test_address_conversion.py` は、`address_geocode.ipynb` のセル（設定→ABRマスター準備→住所変換ロジック
定義→住所CSV変換・出力）をそのまま順に実行する回帰テスト（Colabのアップロード・ダウンロードだけを
差し替える）。ABRデータは公式データをコミットできないため、実データと同じ列構成の**完全な架空データ**
（町字・地番・地番位置参照と、県単位を想定して別の自治体の行も混ぜた住居表示の街区・住居と位置参照）を
テスト内でZIPにして使う。次を確認する。

* 住所表記の揺れ（全角／半角、漢数字の丁目、ハイフン類、空白、「字」「大字」の有無、`番地`/`番`/`号`/`の`
  の表記差）が同じ地番・同じ座標へ変換されること。方書の無い地番住所の状態・座標が、住居表示・方書対応の
  前と変わらないこと
* 住居表示住所を街区符号・住居番号（・住居番号2）の完全一致で照合し、住居位置参照の座標を使うこと
  （街区だけの一致・街区位置参照・近い番号では座標を付けないこと）。3段の番号は3段の完全一致を優先し、
  空白区切りの方書を除外した住所で0件の場合だけ、3段目を除いた住居が一意にあり座標もある等の条件を満たす
  ときに3段目を除外すること（方書の無い3段の住所では除外しないこと）
* 方書付き住所を、元の住所の空白の位置で方書を外して照合すること（空白の無い番号は切り詰めないこと）
* 県単位のデータから対象自治体だけを使うこと、住居表示データが不完全・不整合なら止まること
* 別の住所を同一視しないこと、出力CSVが5列のままで `address` が入力のままであること

`test_shelter_assignment.py` は、避難所候補・本人住所のハザード判定・ハザード大分類別の人数集計・結果CSVの組み立て
（`src/assignment/shelter_assignment.py`）の回帰テスト（架空の要支援者7人・避難所5件・ハザード区域5枚）。次を確認する。

* 結果CSVの列が正式な14列で、この順であること。入力CSVの独自の列は14列の後ろへ残ること。旧版の
  `candidate_N_disaster_support`・避難所地点のハザード・直線交差のハザードの列が出ないこと。前回の結果列（旧版の列を含む）
  は置き換え、職員が追加した列は残すこと
* 候補1〜3が `geopy.distance.geodesic` の直線距離順で、距離が同じ場合は避難所名順であること。ハザードの有無・避難所の
  ハザード・避難所の災害種別の列によって順位が変わらないこと
* 空間判定の対象が、有効な座標を持つ本人の地点（点）だけであること
* 座標が使えない行が削除されず、ハザード結果・候補・距離が空欄になること
* ハザード別の集計: 同じ大分類の複数ポリゴンに該当しても1人／複数の大分類に該当する人は各分類に1人ずつ／「いずれかに
  該当」は人物単位で重複なし／「ハザード該当なし」は有効な座標で判定して該当が無かった人だけ（座標未取得は含めない）／
  集計は大分類（`hazard_category`）で行い表示用の文字列を解析しないこと

`test_hazard_loader.py` は、ハザードデータの読込（`src/hazard/hazard_loader.py`）で、公式配布形式のファイル名から
大分類（津波・高潮・洪水・土砂災害）と詳細区分が決まること（洪水は河川区分が違っても大分類は「洪水」）、
自動判定できないファイルでは入力した種別名が大分類になることを確認する。

`test_review_html.py` は、レビュー用HTML（`src/review/review_builder.py`・`review_template.html`）の回帰テスト。
集計値・本人のハザード・候補1〜3と直線距離が表示用データに入ること、道路経路・避難所側のハザード・直線交差のハザード・
避難所の災害種別対応の表示が画面（HTML・CSS・JavaScript）に無いこと、集計や候補がCSVと食い違うときはHTMLを作らずに
止まることを確認する。ブラウザでの見た目・操作は自動テストの対象外で、実機確認として行う。

`test_notebook_end_to_end.py` は、`sheltermatch.ipynb` のセルを外部通信なしで最後まで実行して、結果CSV・レビューZIP・
集計が揃うことを確認する。Notebook のセルと取得先の版（`SHELTERMATCH_CODE_REF`。現在は `v1.2.0`）は変更せず、
GitHub からの取得だけを作業ツリーの `src/` へ差し替える（`notebook_harness.py`）。外部通信は GitHub の取得と BODIK API
（いずれも差し替え）だけで、OpenStreetMap 等の道路データを取得しないこと、Notebook に道路経路・災害種別・可変の候補数
の記述が無いことも確認する。

`test_notebook_module_source.py` は、`sheltermatch.ipynb` が Google Colab 実行時に取得する外部モジュール（`src/`）の
取得先の回帰テスト。取得する版（`SHELTERMATCH_CODE_REF`）が `v1.2.0` であること、`main` の `src/` を取得先に
使わないこと、`hazard_loader.py`・`shelter_assignment.py`・`review_builder.py`・`review_template.html` が同じ取得元から
取得されること（`road_routes.py` は取得しない）、API互換性の確認（v1.0.0 時点の旧APIのモジュールとの組み合わせを含め、
互換性のないモジュールでは止まる）と取得失敗時の表示を確認する。「外部モジュール
準備」セルをそのまま実行し、`requests.get` だけを差し替える（外部通信は行わない）。取得する版を更新するときは、
このテストの期待値も意図した変更として一緒に更新する。

## ファイル

* `test_address_conversion.py`: 住所変換の回帰テスト(上記「自動テスト」を参照)。
* `test_shelter_assignment.py`: 避難所候補・本人住所のハザード判定・集計・結果CSVの回帰テスト。
* `test_hazard_loader.py`: ハザードの大分類に関する読込の回帰テスト。
* `test_review_html.py`: レビュー用HTMLの回帰テスト。
* `test_notebook_end_to_end.py`: `sheltermatch.ipynb` の通し実行の回帰テスト。
* `test_notebook_module_source.py`: `sheltermatch.ipynb` の外部モジュールの取得先の回帰テスト。
* `test_municipality_config.py`: 自治体ごとの設定ファイル（`configs/`）と読込モジュールの回帰テスト（設定の検証、JSONと
  Notebook既定値の優先順位、取得失敗・不正値での停止、通常運用のNotebookが外部モジュールと設定ファイルを同じ版から
  取得すること）。外部通信は行わない。
* `test_gsi_shelter_compare.py` / `test_gsi_shelter_compare_notebook.py`: 比較実験（`experiments/gsi_shelter_compare/`）の
  比較ロジックと、Notebookの静的な確認。外部通信は行わない。
* `fake_data.py`: テスト用の完全な架空データ（要支援者・避難所・ハザード区域）。実在のデータではない。
* `notebook_harness.py`: Notebook を外部通信なしで実行するための共通部品（`google.colab` と外部取得の差し替え）。
* `requirements.txt`: 自動テストに必要なライブラリ（GitHub Actions でも使用）。
* `residents_sample_enriched.csv`: 回帰確認用の架空要支援者CSV。共通住民CSVの5列
  (`resident_id,address,latitude,longitude,geocode_status`) に、確認内容を書いた `note` 列を
  追加したもの。`note` のような追加列は、`sheltermatch.ipynb` が正式な14列の後ろへそのまま結果CSVへ引き継ぐ。
* `assigned_shelters.csv`: 上記CSVを実際にGoogle Colabで処理した結果の記録（**v1.0.0 時点の出力形式**。避難所の
  災害種別対応・避難所地点のハザード等の列を含む旧形式で、v1.1.0 の正式な14列とは異なる。回帰テストでは使っていない）。共通住民CSVを5列へ
  統一する前に取得したものなので、`geocode_status` 列は含まれていない(当時の入力には無かったため)。
  再実行した場合は `geocode_status` 列が1つ増える。

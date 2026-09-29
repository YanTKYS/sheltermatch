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

実際の Google Colab での実行、OpenStreetMap の道路データ取得、公式ハザードデータ、実ブラウザでの
レビュー画面の確認（`experiments/road_routes/`）は CI の対象外で、従来どおり実機確認として行う。

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

`test_road_routes.py` は、道路に沿った参考経路（`src/review/road_routes.py`）とレビューHTMLへの
受け渡しの回帰テスト。外部通信は行わず、OSMnx が返すグラフと同じ形の小さな架空の道路網で、道路の形に
沿うこと・networkx と同じ最短距離になること・遠すぎる道路へ接続しないこと・つながらない場合や
取得範囲の端では経路を作らないこと等を確認する。実際の道路データでの確認は
[docs/road-routes-check.md](../docs/road-routes-check.md) を参照。

## ファイル

* `test_address_conversion.py`: 住所変換の回帰テスト(上記「自動テスト」を参照)。
* `test_road_routes.py`: 道路に沿った参考経路の回帰テスト(上記「自動テスト」を参照)。
* `requirements.txt`: 自動テストに必要なライブラリ（GitHub Actions でも使用）。
* `residents_sample_enriched.csv`: 回帰確認用の架空要支援者CSV。共通住民CSVの5列
  (`resident_id,address,latitude,longitude,geocode_status`) に、確認内容を書いた `note` 列を
  追加したもの。`note` のような追加列は、`sheltermatch.ipynb` がそのまま結果CSVへ引き継ぐ。
* `assigned_shelters.csv`: 上記CSVを実際にGoogle Colabで処理した結果の記録。共通住民CSVを5列へ
  統一する前に取得したものなので、`geocode_status` 列は含まれていない(当時の入力には無かったため)。
  再実行した場合は `geocode_status` 列が1つ増える。

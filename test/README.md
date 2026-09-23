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
python3 -m unittest discover -s test
```

`test_address_conversion.py` は、`address_geocode.ipynb` の住所変換ロジックをNotebookのセルから
そのまま読み込んで実行する回帰テスト。ABRマスターは公式データをコミットできないため、実データと
同じ列構成の架空データをテスト内で用意している。実データで起こり得る住所表記（全角／半角、漢数字の
丁目、ハイフン類、空白、「字」「大字」の有無、`番地`/`番`/`号`/`の` の表記差）が同じ地番・同じ座標へ
変換されることと、別の住所を同一視していないことを確認する。

`test_road_routes.py` は、道路に沿った参考経路（`src/review/road_routes.py`）とレビューHTMLへの
受け渡しの回帰テスト。外部通信は行わず、OSMnx が返すグラフと同じ形の小さな架空の道路網で、道路の形に
沿うこと・networkx と同じ最短距離になること・遠すぎる道路へ接続しないこと・つながらない場合や
取得範囲の端では経路を作らないこと等を確認する。実際の道路データでの確認は
[docs/road-routes-check.md](../docs/road-routes-check.md) を参照。

## ファイル

* `test_address_conversion.py`: 住所変換の回帰テスト(上記「自動テスト」を参照)。
* `test_road_routes.py`: 道路に沿った参考経路の回帰テスト(上記「自動テスト」を参照)。
* `residents_sample_enriched.csv`: 回帰確認用の架空要支援者CSV。共通住民CSVの5列
  (`resident_id,address,latitude,longitude,geocode_status`) に、確認内容を書いた `note` 列を
  追加したもの。`note` のような追加列は、`sheltermatch.ipynb` がそのまま結果CSVへ引き継ぐ。
* `assigned_shelters.csv`: 上記CSVを実際にGoogle Colabで処理した結果の記録。共通住民CSVを5列へ
  統一する前に取得したものなので、`geocode_status` 列は含まれていない(当時の入力には無かったため)。
  再実行した場合は `geocode_status` 列が1つ増える。

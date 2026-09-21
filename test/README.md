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

## ファイル

* `residents_sample_enriched.csv`: 回帰確認用の架空要支援者CSV。共通住民CSVの5列
  (`resident_id,address,latitude,longitude,geocode_status`) に、確認内容を書いた `note` 列を
  追加したもの。`note` のような追加列は、`sheltermatch.ipynb` がそのまま結果CSVへ引き継ぐ。
* `assigned_shelters.csv`: 上記CSVを実際にGoogle Colabで処理した結果の記録。共通住民CSVを5列へ
  統一する前に取得したものなので、`geocode_status` 列は含まれていない(当時の入力には無かったため)。
  再実行した場合は `geocode_status` 列が1つ増える。

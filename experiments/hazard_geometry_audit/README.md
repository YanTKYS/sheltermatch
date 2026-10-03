# hazard_geometry_audit/

公式配布のハザードデータのうち、`sheltermatch.ipynb` が「空・不正なジオメトリ」として区域判定から除外している
レコードが、実際には何なのかを調べるための **調査専用Notebook** です
（[`hazard_geometry_audit.ipynb`](hazard_geometry_audit.ipynb)）。

* **sheltermatch本体ではありません。** 避難所候補の算出や、結果CSVの作成は行いません。
* **公式ハザードデータの品質確認用**で、**本体のデータを自動修復するものではありません。**
  `shapely.make_valid()` などは、このNotebookの中だけで試す調査用です。
* 調べる内容: 除外されたジオメトリの実態（null / empty / `is_valid=False` の内訳）、`make_valid()` で
  Polygon / MultiPolygon へどの程度復元できるか、復元した場合にハザード判定結果が変わり得るか。
  このNotebookは判断材料を出すところまでを担当し、「本体へ導入すべき」といった結論は出しません。
* 調査の結果、本体でどう扱うことにしたかは、[docs/hazard-data.md](../../docs/hazard-data.md) の
  「ジオメトリの扱い」を参照してください。

## 使い方と注意

Google Colab で、公式ハザードZIPと要支援者CSV（5列）をアップロードして上から順に実行します（詳しくはNotebook冒頭）。

* 公式データの自動ダウンロードは行いません。アップロードしたファイルだけを使います。
* 要支援者CSVには個人情報が含まれ得ます。利用を許可された環境でのみ扱ってください。
* **実際の要支援者データ・公式データ本体・実行結果は、リポジトリへコミットしません。**

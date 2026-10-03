# address_geocoding/

**住所→座標変換方式の検証記録**です。`address_geocode.ipynb` の変換方式を決めるまでに、3つの方式をそれぞれ検証した
PoC（試作）のNotebookを、そのまま残しています。**いずれも本番Notebookではありません**
（`sheltermatch.ipynb`・`address_geocode.ipynb` の通常運用では使いません）。

| ファイル | 検証した方式 |
| --- | --- |
| [`jageocoder_poc.ipynb`](jageocoder_poc.ipynb) | Jageocoder による変換が、現在のColabで実用可能かの確認（PyPI公開版と jageocoder-converter 公式GitHub最新版の2経路を、隔離環境で比較） |
| [`abr_geocoder_poc.ipynb`](abr_geocoder_poc.ipynb) | デジタル庁公式 [`abr-geocoder`](https://github.com/digital-go-jp/abr-geocoder)（`abrdb` + `abrg` + PostgreSQL + DuckDB）が、Colab上で成立するかの確認（沖縄県・座標付きに限定） |
| [`abr_csv_geocode_poc.ipynb`](abr_csv_geocode_poc.ipynb) | ABR公式CSV（町字マスタ・地番マスタ・位置参照拡張）を、利用者が手動で取得してアップロードし、直接照合する方式の確認（地番住所のみが対象のPoC） |
| [`address_conversion_test_input.csv`](address_conversion_test_input.csv) | 住所変換の動作確認に使った、個人情報を含まない検証用サンプル（`resident_id,address,note`） |

## 現在の `address_geocode.ipynb` との関係

通常運用の [`address_geocode.ipynb`](../../address_geocode.ipynb) は、このうち **ABR公式CSVを直接照合する方式**
（`abr_csv_geocode_poc.ipynb` で検証したもの）を土台に、住居表示住所・方書付き住所への対応などを加えたものです。
Jageocoder と公式 abr-geocoder は、辞書・キャッシュ生成のための配布データの自動取得が、クラウド環境で403などの
エラーになったこと（Jageocoder にはさらにPython・converter APIの互換性問題もあった）から採用していません
（[docs/project-status.md](../../docs/project-status.md) の「この方式を選んだ理由」）。

## 注意

* ここにあるNotebookは検証の記録です。PoCの時点の内容のままで、通常運用の手順や仕様の根拠としては
  [docs/address-data.md](../../docs/address-data.md)・[docs/project-status.md](../../docs/project-status.md) を参照してください。
* ABR公式データ・実際の住所データは、リポジトリへコミットしません。

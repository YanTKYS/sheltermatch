# experiments/

本番処理ではない、**検証・調査・PoC（試作）** を置く場所です。

* 通常運用の対象は、リポジトリ直下の [`sheltermatch.ipynb`](../sheltermatch.ipynb)・
  [`address_geocode.ipynb`](../address_geocode.ipynb) と、[`src/`](../src/) です。ここにあるコードは通常運用では使いません
  （本番コードから `experiments/` を import することもありません）。
* **実験コードがあることは、その方式を本番採用していることを意味しません。** 採用した方式・しなかった理由は
  [docs/project-status.md](../docs/project-status.md) を参照してください。
* 実際の要支援者データ・住所データなどの個人情報、公式データ本体、実行結果は、いかなる形式でもコミットしません。
  ここに置くデータは、完全な架空データ、または個人情報を含まない検証用サンプルだけです。

| フォルダ | 内容 |
| --- | --- |
| [`address_geocoding/`](address_geocoding/README.md) | 住所→座標変換方式のPoC・比較検証（Jageocoder / 公式 abr-geocoder / ABR公式CSV直接照合） |
| [`hazard_geometry_audit/`](hazard_geometry_audit/README.md) | 公式ハザードデータの空・不正ジオメトリの調査 |
| [`performance/`](performance/README.md) | 実運用規模を想定した架空データ（100 / 500 / 1000件）による性能確認 |
| [`gsi_shelter_compare/`](gsi_shelter_compare/README.md) | BODIK と国土地理院の指定緊急避難場所データの比較（差異の把握が目的で、どちらが正しいかは判定しない） |

各実験の目的・位置づけ・使い方は、各フォルダの README を参照してください。

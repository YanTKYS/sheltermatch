# performance/

実運用規模を想定した処理時間・出力内容の確認に使う、**性能確認用の架空データ**です。

| ファイル | 内容 |
| --- | --- |
| `residents_100.csv` / `residents_500.csv` / `residents_1000.csv` | 架空の共通住民CSV（100 / 500 / 1000件。正式な5列 `resident_id,address,latitude,longitude,geocode_status`） |
| [`generate_performance_samples.py`](generate_performance_samples.py) | 上の3ファイルを再生成するスクリプト（固定シードのため、実行するたびに同じ内容になる） |

* **実在する住所・住民データではありません。** 座標は糸満市周辺として妥当な範囲の架空の値で、座標欠損・範囲外などの
  異常系を少数含みます。
* 目的は、主として `sheltermatch.ipynb`（本体）の実運用規模での処理時間・出力の確認です。
  `address_geocode.ipynb` は通さないため、住所→座標変換の正確性や性能の検証ではありません。
* 確認結果は [docs/performance-test.md](../../docs/performance-test.md)（v1.0.0 時点の記録）を参照してください。

## 再生成

```bash
python3 experiments/performance/generate_performance_samples.py
```

`numpy` が必要です。実行すると、このフォルダの 3 つのCSVを上書きします。

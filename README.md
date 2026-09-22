# sheltermatch

要支援者ごとに、**距離が近い避難所候補**と、その判断に必要な情報（距離・避難所の災害種別対応・
ハザード区域との位置関係）を整理し、職員が避難先を判断するための一次資料を作成するツールです。

Google Colab で動く Notebook として提供しています。

> 避難所を機械的に決定するツールではありません。最終的な避難先は職員が判断します。

## 何をするツールか

| | |
| --- | --- |
| **目的** | 要支援者ごとに避難所候補を整理し、職員が避難先を判断するための一次資料を作る |
| **利用場面** | 要支援者名簿をもとに、平常時に避難先の検討資料を準備する |
| **入力** | 要支援者一覧CSV（5列）＋ 避難所一覧（BODIK Data API）＋ ハザード区域データ（任意） |
| **出力** | `assigned_shelters.csv`（正式な成果物）と `sheltermatch_review.zip`（地図で確認する補助成果物） |

### 行わないこと

- 避難先・避難経路の自動決定
- 道路経路・通行可能性の計算（距離は直線距離で、道路距離ではありません）
- ハザード判定結果による候補の自動除外・自動順位変更

距離順位とハザード情報は、別々の情報として並べて出力します。

## 入力データ

### 要支援者一覧CSV（共通住民CSV）

次の5列です。テンプレート: [`templates/residents.csv`](templates/residents.csv)

```text
resident_id,address,latitude,longitude,geocode_status
```

- `resident_id` は空欄・重複不可（住所変換の結果と割当結果を紐づける結合キーのため）
- `latitude` / `longitude` は行ごとに空欄・不正でも構いません。該当行は候補算出の対象外になりますが、
  **行は削除されず、入力した値もそのまま結果CSVへ残ります**
- 住所しかない場合は、先に `address_geocode.ipynb` で緯度・経度を付与してください

### 避難所一覧

BODIK Data API から糸満市の自治体標準オープンデータセット（指定緊急避難場所）を取得します。
取得に失敗した場合は、CSVアップロードへ自動的に切り替わります。

名称・緯度・経度のほか、`災害種別_` で始まる列から災害種別ごとの対応区分
（`1`=対応済み / `2`=2階以上であれば対応済み / 空欄=未対応）を保持します。

### ハザード区域データ（任意）

`ENABLE_HAZARD_CHECK = True` のときだけ使います。`.geojson` 単体、または国・県等の公式配布ZIPを
そのままアップロードできます（展開・変換は不要です）。取得元と操作手順は
[docs/hazard-data.md](docs/hazard-data.md) を参照してください。

## 処理の概要

1. **要支援者CSVを読み込む** — 5列を確認し、座標の状態を `match_status` として判定する
   （`ok` / `no_coordinates`（空欄）/ `invalid_coordinates`（値はあるが使えない））
2. **避難所一覧を取得する** — 日本語列名を内部標準列名へ正規化し、座標が使えない避難所を除く
3. **ハザード区域データを読み込む**（任意）— EPSG:4326 の Polygon / MultiPolygon へ統合する
4. **避難所候補を算出する** — 要支援者と各避難所の直線距離（`geopy.distance.geodesic`）を計算し、
   近い順に上位 `TOP_N` 件（既定3件）を候補とする。距離が同じ場合は避難所名で順序を安定させる
5. **ハザード判定を行う**（任意）— 要支援者地点・候補避難所地点が区域の内部または境界上にあるか、
   両地点を結ぶ直線が区域と交差するかを判定する（道路上の避難経路の判定ではありません）
6. **結果を出力する** — 結果CSVと、地図で目視確認するためのレビュー用HTMLを出力する

ハザード判定の列は、有効な座標で実際に判定できた場合だけ `True` / `False` になります。
空欄は「判定できなかった」であり、「区域外」ではありません。

## 出力物

### `assigned_shelters.csv`（正式な成果物）

入力CSVの全列に、次の列を加えたものです（UTF-8 BOM付き。Excelで開けます）。

| 列 | 内容 |
| --- | --- |
| `match_status` | 座標の状態（`ok` / `no_coordinates` / `invalid_coordinates`） |
| `resident_in_hazard` / `resident_hazard_types` | 要支援者地点のハザード判定 |
| `candidate_N` / `distance_N_m` | 候補避難所名と直線距離（メートル） |
| `candidate_N_disaster_support` | その避難所の災害種別対応区分 |
| `candidate_N_shelter_in_hazard` ほか | 候補避難所地点・直線交差のハザード判定 |

### `sheltermatch_review.zip`（補助成果物）

展開して `review.html` を開くと、要支援者を1人ずつ選んで、要支援者地点・候補避難所1〜3・両者を結ぶ
直線・ハザード区域を地図で確認できます。**地図ライブラリ・背景地図・ハザード画像をすべて同梱するため、
展開したフォルダだけで外部通信なしに開けます**（閉域環境での利用を想定）。

正式な割り当て結果ではなく、結果CSVを目視確認するための補助成果物です。

## 実行方法

1. `sheltermatch.ipynb` を Google Colab で開く
2. 冒頭の「利用者設定」セルを確認する

   ```python
   ENABLE_HAZARD_CHECK = False   # ハザード区域との位置関係も確認する場合は True
   TOP_N = 3                     # 要支援者ごとに算出する避難所候補の件数
   SHELTER_SOURCE = "api"        # "api"=BODIKから取得 / "csv"=CSVをアップロード
   ```

3. 「ランタイム → すべてのセルを実行」
4. 画面の指示に従って、要支援者CSV（と、必要ならハザードデータ）をアップロードする
5. `assigned_shelters.csv` と `sheltermatch_review.zip` をダウンロードする

住所しかない場合は、先に `address_geocode.ipynb` で住所→座標変換を行います。ABR公式CSV3種類を
アップロードし、住所CSVを変換して、同じ5列のCSVを出力します。手順は
[docs/address-data.md](docs/address-data.md) を参照してください。

```text
templates/residents.csv
  ↓
address_geocode.ipynb（住所→座標変換）
  ↓  同じ5列のCSV
sheltermatch.ipynb（避難所候補の算出）
  ↓
assigned_shelters.csv / sheltermatch_review.zip
```

## ディレクトリ構成

| パス | 内容 |
| --- | --- |
| `sheltermatch.ipynb` | 本体。避難所候補の算出からCSV・レビューHTML出力まで |
| `address_geocode.ipynb` | 前処理。ABR公式CSVを使った住所→座標変換 |
| `src/hazard/hazard_loader.py` | ハザードデータの読込・正規化・統合 |
| `src/review/review_builder.py` | レビュー成果物（PNG・HTML・ZIP）の生成 |
| `src/review/review_template.html` | `review.html` の画面（HTML / CSS / JavaScript） |
| `templates/residents.csv` | 要支援者一覧CSVのテンプレート |
| `test/` | 回帰テストと、個人情報を含まない確認用サンプル |
| `experiments/` | 方式検証の記録と、性能確認用の架空データ |
| `docs/` | データ取得手順・現状整理などの詳細ドキュメント |

`sheltermatch.ipynb` は、Google Colab での実行時に `src/` 配下のファイルを GitHub の `main` から
取得して読み込みます。Notebook には職員が行う操作だけを残し、実装の詳細は通常のPython / HTMLファイル
として保守しています（外部への通信が発生するのは成果物を生成するときだけで、できあがった
`sheltermatch_review.zip` は外部通信なしで利用できます）。

## テスト

住所変換の回帰テストを用意しています。

```bash
python3 -m unittest discover -s test
```

`address_geocode.ipynb` の住所変換ロジックをそのまま読み込み、実データで起こり得る住所表記
（全角／半角、漢数字の丁目、ハイフン類、空白、「字」「大字」の有無、`番地`/`番`/`号`/`の` の表記差など）が
同じ地番・同じ座標に変換されること、および別の住所を同一視していないことを確認します。

Notebook 全体の通し確認は Google Colab 上で行います。過去の確認結果は
[docs/operation-check.md](docs/operation-check.md)・[docs/performance-test.md](docs/performance-test.md)
を参照してください。

## 実データを扱うときの注意

- **実際の要支援者データ・住所データは、いかなる形式でもこのリポジトリへコミットしないでください。**
  サンプルを追加する場合は完全な架空データを使用してください
- 出力される `assigned_shelters.csv` と `sheltermatch_review.zip` には住所・座標等の個人情報が
  含まれ得ます。保存先・共有方法・保管期間を、所属団体の規程に従って取り扱ってください
- 国・県等から取得した公式GISデータ本体（GeoJSON・Shapefile ZIP等）もコミットしません。
  必要になった時点で公式配布元から取得してください
- 変換・算出できなかった行は削除されず、`geocode_status` / `match_status` に理由が残ります。
  **曖昧な住所を推測して `matched` 扱いにすることはありません。** 該当行は元データを確認して
  対応してください
- 距離は直線距離です。道路距離・避難経路ではないため、資料として配布する際は誤解されないよう
  ご注意ください

## ドキュメント

| ドキュメント | 内容 |
| --- | --- |
| [docs/project-status.md](docs/project-status.md) | 現状整理・開発方針・実装済み機能の詳細 |
| [docs/address-data.md](docs/address-data.md) | ABR公式データの取得手順と `geocode_status` の読み方 |
| [docs/hazard-data.md](docs/hazard-data.md) | ハザードデータの取得元と投入手順 |
| [docs/operation-check.md](docs/operation-check.md) | 操作導線・エラー処理の確認記録 |
| [docs/performance-test.md](docs/performance-test.md) | 処理時間の確認記録 |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |

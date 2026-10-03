# 自治体ごとの設定ファイル（configs/）

`sheltermatch` の Notebook が使う、**自治体ごとの設定**をまとめたJSONです。通常の利用では Notebook を編集せず、
各 Notebook の冒頭にある `CONFIG_NAME`（既定: `"itoman-city"`）の設定ファイルが自動で読み込まれます。

```
configs/
└─ itoman-city.json    ← 糸満市（既定）
```

## 設定ファイルの役割

設定ファイルは、次の2つをまとめるものです。

* **自治体そのものの設定**: 自治体名・自治体コード・BODIK のデータセット（resource_id）
* **その自治体で通常使う Notebook の設定**: `sheltermatch.ipynb` の `ENABLE_HAZARD_CHECK`・`SHELTER_SOURCE`、
  比較実験 `gsi_shelter_compare.ipynb` の `TILE_RING`・`EXACT_MAX_DISTANCE_M`・`NEAR_DISTANCE_M`

内部のAPIバージョン、GSIのレイヤー定義、候補算出・住所変換のルールなど、**自治体ごとに利用者が変える必要のない内部の値は
設定ファイルへ入れません**（コードに残します）。

## 形（schema_version 1）

```json
{
  "schema_version": 1,
  "municipality": { "id": "itoman-city", "name": "糸満市", "code": "47210", "prefecture": "沖縄県" },
  "bodik": { "resource_id": "3132a0a4-f522-4b2d-bf18-f106d8b3a5ae" },
  "sheltermatch": { "enable_hazard_check": true, "shelter_source": "api" },
  "gsi_shelter_compare": { "tile_ring": 1, "exact_max_distance_m": 30, "near_distance_m": 100 }
}
```

| キー | 必須 | 内容 |
| --- | :---: | --- |
| `schema_version` | ○ | `1` のみ対応 |
| `municipality.id` | ○ | 設定名と同じ値（ファイル名 `<id>.json` と一致。取り違え防止のため確認する） |
| `municipality.name` | ○ | 自治体名（例: `糸満市`）。住所の判定・変換、比較実験の対象範囲の判定に使う |
| `municipality.code` | ○ | 5桁の全国地方公共団体コードの文字列（検査数字なし。例: `"47210"`）。6桁の `lg_code`（`472107`）は検査数字から計算する |
| `municipality.prefecture` | 任意 | 都道府県名（例: `沖縄県`）。`address_geocode.ipynb` の住所表記の判定に使う |
| `bodik.resource_id` | ○ | BODIK Data API（CKAN）の避難所データの resource_id（UUID） |
| `sheltermatch.enable_hazard_check` | 任意 | `true` / `false`（真偽値のみ。`"true"` や `1` は受け付けない） |
| `sheltermatch.shelter_source` | 任意 | `"api"` または `"csv"` |
| `gsi_shelter_compare.tile_ring` | 任意 | 0以上の整数 |
| `gsi_shelter_compare.exact_max_distance_m` | 任意 | 0より大きい数値 |
| `gsi_shelter_compare.near_distance_m` | 任意 | 0より大きい数値 |

未知のキーは、綴り間違いを黙って無視しないよう、エラーにします。

## 値の優先順位

各 Notebook は、自分の既定値（例: `ENABLE_HAZARD_CHECK = False`）を持っています。

1. 設定ファイルに値がある → **設定ファイルの値**を使う
2. 設定ファイルにそのキーが無い → **Notebook の既定値**を使う

実行時には、最終的な値と設定元を表示します。

```
対象自治体: 糸満市
自治体コード: 47210
設定ファイル: configs/itoman-city.json

利用者設定:
  ENABLE_HAZARD_CHECK = True [JSON]
  SHELTER_SOURCE      = api  [JSON]
```

## 設定ファイルが取得できない・不正なとき

「キーが無い」と「設定ファイルを取得・解釈できない」は区別します。

* **キーが無い**: Notebook の既定値で続行します。
* **設定ファイルが無い（404）・通信できない・JSONの構文が不正・必須キーが無い・型や値が不正**: 原因を表示して**止まります**。
  Notebook の既定値で続行することはしません（誤った設定のまま業務処理が進むのを防ぐため）。不正な値は補正しません。

## 設定ファイルの場所

ローカル（現在のフォルダか、その上位）に `configs/<CONFIG_NAME>.json` があればそれを使い、無ければ GitHub から取得します
（Google Colab で GitHub 上の Notebook を直接開いた場合は、GitHub から取得します）。`sheltermatch.ipynb`・
`address_geocode.ipynb` は、実行するコードと同じ版（タグ）の設定ファイルを取得し、比較実験の Notebook は `main` から取得します。

## 別の自治体で使う

1. `configs/other-city.json` を追加します（`itoman-city.json` をコピーして、`municipality.id` をファイル名と同じにし、
   自治体名・コード・BODIK の resource_id などを、その自治体の公式な値に書き換えます。**推測で値を入れないでください**）。
2. 各 Notebook の `CONFIG_NAME = "itoman-city"` を `CONFIG_NAME = "other-city"` に変更します。

他の自治体の設定ファイルは、実データに基づく値が確認できてから追加します（現在は糸満市のみです）。
自治体が変わると、ハザードデータ・ABR（住所データ）・避難所データの用意や、住所表記の確認も別途必要です。

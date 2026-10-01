# sheltermatch

要支援者本人の住所についてハザードの影響を確認し、**直線距離が近い避難所候補** を職員へ提示するための
一次資料を作成するツールです。Google Colab で動く Notebook として提供しています。

> 避難所や経路を機械的に決定するツールではありません。最終的な避難先と、実際にどの経路を使うかは、
> 本人への聞き取り等をもとに職員が判断します。

## できること

| | |
| --- | --- |
| **本人住所のハザード確認** | 要支援者本人の住所が、ハザード区域（洪水・土砂災害・津波・高潮など）の中にあるかを確認する |
| **近い避難所候補1〜3** | 本人住所から避難所までの直線距離が近い順に、候補3件を示す |
| **直線距離** | 候補ごとに、本人住所からの直線距離（メートル）を示す |
| **ハザード別の人数集計** | ハザードの大分類（津波・高潮・洪水・土砂災害）ごとに、住所が該当する人数を集計する |

| | |
| --- | --- |
| **入力** | 要支援者一覧CSV（5列）＋ 避難所一覧（BODIK Data API）＋ ハザード区域データ（任意） |
| **出力** | `assigned_shelters.csv`（正式な成果物。14列）と `sheltermatch_review.zip`（地図で確認する補助成果物） |

### 行わないこと

- 避難先・避難経路の自動決定、避難所や経路の安全性の評価
- 避難所の地点・本人と避難所を結ぶ線・道路経路についてのハザード判定（判定するのは本人住所だけ）
- ハザード判定による候補の除外・順位の変更、危険度のスコア化

候補の順位は **本人住所から避難所までの直線距離だけ** で決まります。距離は直線距離で、道路距離ではありません。

## 入力データ

### 要支援者一覧CSV（共通住民CSV）

次の5列です。テンプレート: [`templates/residents.csv`](templates/residents.csv)

```text
resident_id,address,latitude,longitude,geocode_status
```

- `resident_id` は空欄・重複不可（住所変換の結果と割当結果を紐づける結合キーのため）
- `latitude` / `longitude` は行ごとに空欄・不正でも構いません。該当行は候補・ハザードの判定対象外になりますが、
  **行は削除されず、入力した値もそのまま結果CSVへ残ります**
- 住所しかない場合は、先に `address_geocode.ipynb` で緯度・経度を付与してください

### 避難所一覧

BODIK Data API から糸満市の自治体標準オープンデータセット（指定緊急避難場所）を取得します。
取得に失敗した場合は、CSVアップロードへ自動的に切り替わります。使うのは **名称・緯度・経度** だけです
（避難所の災害種別対応などの列は使いません）。

### ハザード区域データ（任意）

`ENABLE_HAZARD_CHECK = True` のときだけ使います。`.geojson` 単体、または国・県等の公式配布ZIPを
そのままアップロードできます（展開・変換は不要です）。取得元と操作手順は
[docs/hazard-data.md](docs/hazard-data.md) を参照してください。

## 出力物

### `assigned_shelters.csv`（正式な成果物）

次の14列です（UTF-8 BOM付き。Excelで開けます）。

| 列 | 内容 |
| --- | --- |
| `resident_id` `address` `latitude` `longitude` `geocode_status` | 入力CSVの値（そのまま） |
| `match_status` | 座標の状態（`ok` / `no_coordinates`（空欄）/ `invalid_coordinates`（値はあるが使えない）） |
| `resident_in_hazard` | 本人住所がハザード区域内（境界上含む）か。`True` / `False` / 空欄 |
| `resident_hazard_types` | 該当したハザードの種別（詳細区分つき。複数あれば `;` 区切り） |
| `candidate_1` `distance_1_m` | 最も近い避難所の名称と直線距離（メートル） |
| `candidate_2` `distance_2_m` | 2番目に近い避難所と直線距離 |
| `candidate_3` `distance_3_m` | 3番目に近い避難所と直線距離 |

- `resident_in_hazard` は、有効な座標で実際に判定できたときだけ `True` / `False` になります。**空欄は「判定できな
  かった」（座標が使えない、またはハザード判定を実施していない）であり、「区域外」ではありません**
- 座標が使えない行は、行を削除せず、`match_status` に理由を残し、ハザード・候補・距離は空欄になります
  （曖昧な座標や近い住所での補完はしません）
- 入力CSVにほかの列（独自の備考列など）があれば、この14列の後ろにそのまま残ります

### ハザード別の人数集計

ハザード判定を行った場合、Notebookの結果表示とレビュー画面の上部に、本人住所についての集計を表示します
（件数は一例）。

```text
総件数              1,000
座標あり              970
座標未取得             30

いずれかに該当        200
ハザード該当なし      770

津波                  120
高潮                   90
洪水                   60
土砂災害               30
```

- 大分類ごとの人数は、その分類に該当する人の数です。**同じ大分類の複数の区域に該当しても1人**、複数の大分類に
  該当する人は **それぞれの大分類に1人ずつ** 数えます。そのため、各分類の合計は「いずれかに該当」と一致しない
  場合があります（「いずれかに該当」は重複なしの人数です）
- 「ハザード該当なし」は、有効な座標があり、判定した結果どのハザードにも該当しなかった人だけです。
  **座標未取得の人は含めません**

### `sheltermatch_review.zip`（補助成果物）

展開して `review.html` を開くと、画面上部でハザード別の人数集計を確認でき、要支援者を1人ずつ選んで、
本人地点・本人住所のハザード・候補避難所1〜3と直線距離・両者を結ぶ直線・ハザード区域を地図で確認できます。
地図の線は距離を見るための **直線** で、実際に通る経路ではありません。

- 検索欄の下のチェックで、一覧を「座標を確認」「ハザード該当あり」の事実で絞り込めます
- 地図左上の「全候補を表示」で、本人の地点と候補1〜3がすべて入る範囲に戻せます
- 画面下部の「候補詳細をたたむ」で候補表を隠し、地図を広く使えます

**地図ライブラリ・背景地図・ハザード画像をすべて同梱するため、展開したフォルダだけで外部通信なしに開けます**
（閉域環境での利用を想定）。正式な割り当て結果ではなく、結果CSVを目視確認するための補助成果物です。

## 実行方法

1. `sheltermatch.ipynb` を Google Colab で開く
2. 冒頭の「利用者設定」セルを確認する

   ```python
   ENABLE_HAZARD_CHECK = False   # 本人住所のハザードも確認する場合は True
   SHELTER_SOURCE = "api"        # "api"=BODIKから取得 / "csv"=CSVをアップロード
   ```

3. 「ランタイム → すべてのセルを実行」
4. 画面の指示に従って、要支援者CSV（と、必要ならハザードデータ）をアップロードする
5. `assigned_shelters.csv` と `sheltermatch_review.zip` をダウンロードする

住所しかない場合は、先に `address_geocode.ipynb` で住所→座標変換を行います。地番住所・住居表示住所と、
それらの後ろに施設名・建物名・部屋番号等（方書）が付いた住所を変換できます（`address` 列は書き換えません）。
ABR公式データのZIPをアップロードし、住所CSVを変換して、同じ5列のCSVを出力します。取得元・手順・結果
（`geocode_status`）の読み方は [docs/address-data.md](docs/address-data.md) を参照してください。
ABRデータはこのリポジトリに保管しません。

```text
templates/residents.csv
  ↓
address_geocode.ipynb（住所→座標変換）
  ↓  同じ5列のCSV
sheltermatch.ipynb（避難所候補の算出・ハザードの確認・集計）
  ↓
assigned_shelters.csv / sheltermatch_review.zip
```

## ディレクトリ構成

| パス | 内容 |
| --- | --- |
| `sheltermatch.ipynb` | 本体。避難所候補・ハザード判定・集計からCSV・レビューHTML出力まで |
| `address_geocode.ipynb` | 前処理。ABR公式CSVを使った住所→座標変換 |
| `src/assignment/shelter_assignment.py` | 避難所候補（直線距離）・本人住所のハザード判定・ハザード大分類別の集計・結果CSVの組み立て |
| `src/hazard/hazard_loader.py` | ハザードデータの読込・正規化・統合 |
| `src/review/review_builder.py` | レビュー成果物（PNG・HTML・ZIP）の生成 |
| `src/review/review_template.html` | `review.html` の画面（HTML / CSS / JavaScript） |
| `templates/residents.csv` | 要支援者一覧CSVのテンプレート |
| `test/` | 回帰テストと、個人情報を含まない確認用サンプル |
| `experiments/` | 方式検証の記録と、性能確認用の架空データ |
| `docs/` | データ取得手順・現状整理などの詳細ドキュメント |

`sheltermatch.ipynb` は、Google Colab での実行時に `src/` 配下のファイルを GitHub から取得して読み込みます。
取得するのは版（タグ。`SHELTERMATCH_CODE_REF`）で固定しており、現在は `v1.1.0` です。実行時に
「外部モジュール: v1.1.0」と表示します。`main` へ変更が入っても、取得する版を更新するまで実行されるコードは
変わりません（版の更新方法は [docs/project-status.md](docs/project-status.md) の「6.4」）。
PR #49 より前に Google Drive 等へ保存した Notebook は、取得する版が `v1.0.0` のままの場合があります。通常運用では
最新の `main` の Notebook を使い、実行時に「外部モジュール: v1.1.0」と表示されることを確認してください。

## テスト

```bash
python3 -m unittest discover -s test
```

住所変換・候補算出・本人住所のハザード判定とハザード別の集計・結果CSVの列・レビューHTMLの内容・Notebookの
通し実行・外部モジュールの取得先の回帰テストを用意しています。すべて完全な架空データを使い、外部通信は行いません
（詳細は [test/README.md](test/README.md)）。Notebook 全体の Google Colab での通し確認の結果は、
[docs/project-status.md](docs/project-status.md) の「7.2 実機確認の実績」を参照してください。

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
- 候補の距離は直線距離です。道路距離・避難経路ではないため、資料として配布する際は誤解されないよう
  ご注意ください

## v1.0.0 からの変更

v1.1.0 は、担当部署の業務要件に合わせて、必要な情報へ絞り込んだ版です（機能の追加ではありません）。
v1.0.0 で実装・確認した道路経路・避難所側のハザード判定・避難所の災害種別対応などは、通常運用から外しました。
内容と理由は [docs/notes/v1.0.0-extended-features.md](docs/notes/v1.0.0-extended-features.md)、変更点は
[CHANGELOG.md](CHANGELOG.md) を参照してください。

## ドキュメント

| ドキュメント | 内容 |
| --- | --- |
| [docs/project-status.md](docs/project-status.md) | 現状整理・開発方針・実装済み機能の詳細 |
| [docs/address-data.md](docs/address-data.md) | ABR公式データの取得手順、`geocode_status` の読み方、ABR更新時の再変換手順 |
| [docs/hazard-data.md](docs/hazard-data.md) | ハザードデータの取得元と投入手順、ハザード別集計の数え方 |
| [docs/notes/v1.0.0-extended-features.md](docs/notes/v1.0.0-extended-features.md) | v1.0.0 で実装し、v1.1.0 の通常運用から外した機能の記録 |
| [docs/operation-check.md](docs/operation-check.md) | 操作導線・エラー処理の確認記録（v1.0.0 時点） |
| [docs/performance-test.md](docs/performance-test.md) | 処理時間の確認記録（v1.0.0 時点） |
| [docs/road-routes-check.md](docs/road-routes-check.md) | 道路に沿った参考経路の確認記録（v1.0.0 時点。通常運用から外した機能） |
| [docs/pre-production-check-2026-09-28.md](docs/pre-production-check-2026-09-28.md) | 実データ投入前の事前確認（サンドボックスでの確認結果と対応案。その後の対応状況・実機確認の結果を追記） |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |

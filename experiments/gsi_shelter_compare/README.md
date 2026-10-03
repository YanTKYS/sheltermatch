# BODIK と国土地理院（GSI）の指定緊急避難場所データの比較実験

`sheltermatch` が現在使っている BODIK の指定緊急避難場所データ（糸満市）と、国土地理院が地理院タイルとして
公開している指定緊急避難場所データ（`skhb01`〜`skhb08`）を比べ、**件数・施設・名称・住所・座標にどの程度差があるか**
を確認するための実験です。

> **通常は [`gsi_shelter_compare.ipynb`](gsi_shelter_compare.ipynb) を Google Colab で開き、上から順に実行します。**
> [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/YanTKYS/sheltermatch/blob/main/experiments/gsi_shelter_compare/gsi_shelter_compare.ipynb)
> （コマンドラインで同じ比較を再現したい場合は、補助として `compare.py` も使えます。→ [実行方法](#実行方法)）

## この実験の位置づけ（必ずお読みください）

* **GSI を正解データとして BODIK を評価する実験ではありません。** 2つの公開データの**差異を把握するための比較**です。
  「どちらが正しい」「どちらへ切り替えるべき」という判断は、このツールは行いません（出力するのは事実の整理だけです）。
* 国土地理院の公式ページには、指定緊急避難場所・指定避難所のデータについて、次の趣旨の注意が示されています。
  * 市町村が登録した情報であること
  * 最新でない場合や、未掲載の場合があること
  * 最新かつ詳細な状況は、当該市町村に確認する必要があること
  * データは随時更新されること
* BODIK 側も自治体が公開するデータであり、どちらも「市町村が登録した情報」が元になっています。差異が見つかっても、
  どちらかの誤りとは限りません（登録時期・整理の粒度・表記ルールの違いなどが考えられます）。
* 本番の `sheltermatch.ipynb`・`src/`・結果CSVの仕様・候補算出ロジックは**変更していません**。本番コードからこの実験を
  import することもありません。データソースの切り替え（`SHELTER_SOURCE = "gsi"`）、GSI を BODIK のフォールバックにすること、
  避難所の災害種別対応の復活なども、この実験の対象外です。

公式資料:

* [国土地理院 地理院タイル一覧（指定緊急避難場所のタイル仕様・注意事項）](https://maps.gsi.go.jp/development/ichiran.html)
* [BODIK 糸満市 指定緊急避難場所データセット](https://data.bodik.jp/dataset/472107_evacuation_space)

## ファイル構成

| ファイル | 内容 |
| --- | --- |
| `gsi_shelter_compare.ipynb` | **比較・確認用の主な入口**（Google Colab 用Notebook）。取得 → 重複整理 → 比較 → 集計表示 → CSV確認・ダウンロードまでを、表（DataFrame）で確認しながら実行する |
| `compare.py` | CLIで同じ比較を再現したい場合の補助。BODIK・GSI の取得（通信）・CSV出力・要約表示の関数を持ち、Notebookもこれらの関数を読み込んで使う |
| `compare_logic.py` | 通信しない比較ロジック（正規化・タイル範囲・GSI内の重複整理・突き合わせ・集計）の**正本**。Notebookにも `compare.py` にもコピーせず、どちらもここを読み込む。`test/test_gsi_shelter_compare.py` が完全な架空データで確認する |
| `output/` | 実行時の成果物（結果CSV）。実行時に作られ、リポジトリにはコミットしない（`.gitignore` 済み） |

## 実行方法

### Notebook（通常はこちら）

1. [`gsi_shelter_compare.ipynb`](gsi_shelter_compare.ipynb) を Google Colab で開きます（上のバッジ、または
   GitHub 上の Notebook から「Open in Colab」）。
2. 「利用者設定」のセルを確認します。通常は変更不要です（`CONFIG_NAME = "itoman-city"`。比較の設定
   `TILE_RING`・`EXACT_MAX_DISTANCE_M`・`NEAR_DISTANCE_M` は Notebook の既定値で、設定ファイルに値があればそちらが
   優先されます）。対象の自治体・BODIK の resource_id・比較設定は、設定ファイル
   [`configs/itoman-city.json`](../../configs/itoman-city.json) から読み込み、実行時に最終値と設定元
   （`[JSON]` / `[Notebook既定値]`）を表示します。設定ファイルを取得・解釈できないときは、既定値で続行せず止まります
   （[`configs/README.md`](../../configs/README.md)）。別の自治体で比較するときは、`CONFIG_NAME` を変更します。
3. 上のセルから順に実行します。比較ロジック（`compare_logic.py`・`compare.py`）は、Notebook の初期セルが GitHub の
   `main`（`COMPARE_LOGIC_REF`）から自動で取得します。同じフォルダ（またはリポジトリを開いている場合は
   `experiments/gsi_shelter_compare/`）に両ファイルがあれば、そちらを使います。
4. Notebook上で、status別件数・`review_needed`（理由別の件数と全件）・`bodik_only`・`gsi_only`（全件）・座標差・
   表記差を表で確認できます。最後のセルが、結果CSV（`gsi_bodik_comparison.csv`・`gsi_unique_shelters.csv`）を
   保存し、Colab ではダウンロードします（Colab 以外では保存だけ行います）。

件数は公開データの更新で変わることがあるため、Notebookには固定値を埋め込んでいません。

### コマンドライン（補助）

```bash
python3 -m pip install requests geopy   # 未導入の場合のみ（Google Colab では geopy の導入だけで足ります）
python3 experiments/gsi_shelter_compare/compare.py
```

公開データの読み取りだけで、APIキーは不要です。主なオプション（`--help` 参照）:

* `--config-name NAME` 自治体の設定名（`configs/NAME.json`。既定: `itoman-city`）
* `--output-dir DIR` 結果CSVの出力先（既定: `output/`）
* `--ring N` BODIK の座標を含むタイルの周囲に追加で取得するタイル数（既定: 設定ファイルの値）
* `--exact-max-m M` `exact_match` とする座標差の上限（既定: 設定ファイルの値）
* `--near-m M` 名称が異なっても `review_needed` にする近接距離（既定: 設定ファイルの値）

優先順位は「コマンドラインの指定 > 設定ファイルの値 > 既定値（設定ファイルに項目が無いとき）」です。

## データの取得方法

### BODIK

`sheltermatch.ipynb`（避難所取得セル）と同じく、`https://data.bodik.jp` の CKAN Data API（`datastore_search`）から、
設定ファイルの `bodik.resource_id`（糸満市は `3132a0a4-…`。糸満市 指定緊急避難場所データセット）を全件取得します。
resource_id と自治体名は `compare.py`・`compare_logic.py` に固定せず、設定ファイルから引数で受け取ります。
比較には 名称・住所・緯度・経度（日本語列名 `名称`・`住所`・`緯度`・`経度`）を使います。方書・災害種別などの
列は使いません。座標が不正な施設も、座標なしのまま比較に残します。

### GSI

* 対象レイヤー: `skhb01` 洪水 / `skhb02` 崖崩れ・土石流・地滑り / `skhb03` 高潮 / `skhb04` 地震 /
  `skhb05` 津波 / `skhb06` 大規模な火事 / `skhb07` 内水氾濫 / `skhb08` 火山現象
  （`sih`・`sfh`（指定避難所）は対象外）
* URL: `https://cyberjapandata.gsi.go.jp/xyz/{レイヤー}/10/{x}/{y}.geojson`（ズームレベル10）
* **タイル範囲**: 日本全国を総当たりせず、BODIK の各施設の座標を含む z=10 タイルを求め、その**周囲1枚ぶん
  （3×3）の隣接タイルも含めて**重複なしで取得します（BODIK に無い近隣の施設を取り逃さないため）。データの無い
  タイルは 404 が返るので空として扱い、それ以外の取得失敗は黙って落とさず異常終了させます。
* 取得した Feature は、まず市町村を問わずすべて保持し、次の「対象範囲の振り分け」で比較対象を決めます。

### GSIの対象範囲の振り分け

住所に自治体名（設定ファイルの `municipality.name`。糸満市なら「糸満市」）が無いことだけを理由に除外はしません。重複整理のあと、施設ごとに `scope` を付けます。

| scope | 条件 | 比較 |
| --- | --- | --- |
| `in_city` | 住所に自治体名を含む | 対象 |
| `address_missing_near` | 住所が空欄で、BODIK の座標範囲（余白約2km）の中にある | 対象（住所は比較できない） |
| `other_address_near_bodik` | 住所に自治体名は無いが、BODIK の施設と名称が一致、またはBODIKの施設から100m以内 | 対象（住所が異なるので `review_needed` になる） |
| `excluded_address_missing_far` | 住所が空欄で、範囲の外 | 対象外 |
| `excluded_other_address` | 上のいずれでもない（他市町村の施設など） | 対象外 |

対象外のものも `gsi_unique_shelters.csv` には `scope` つきで残るので、取りこぼしがないか確認できます。

## GSI内の重複整理

GSI は災害種別ごとにレイヤーが分かれているため、同じ施設が複数レイヤーに出てきます。**正規化後の名称と住所がともに一致し、
座標差が5m以内**のものだけを1施設へ統合し、元のレイヤーを `gsi_layers`（例: `skhb01;skhb03;skhb05`）に残します。

* 名称が空のもの、名称が同じでも住所が違うもの、名称・住所が同じでも座標が離れているもの（座標差が5mを超える）は**統合しません**。
  座標が離れて統合しなかった件数は実行時に表示します。
* `disaster1`〜`disaster8` は比較の参考情報として、統合した Feature の値を `gsi_unique_shelters.csv` に保持します
  （統合した Feature 間で値が食い違う場合は `notes` に記録）。比較の判定には使いません。

## 文字列の正規化

比較用の正規化は最小限です: Unicode NFKC、前後空白の除去、連続空白・全角空白の整理、ハイフン類（`‐ ‑ ‒ – — ― − －` など）の
`-` への統一。長音記号「ー」は変換しません。**施設名・住所から語句を削除したり、`○○小学校` と `○○小` を同じとみなすような
意味的な変換はしません**（`字` の有無も同一視しません）。元の表記（`original_*`）と比較用（`normalized_*`）は別の列に残します。

## 突き合わせのルール

自動的に「同一施設」と断定するのは、**名称・住所が正規化後に一致し、座標差が30m以内の組**だけです（`exact_match`）。
曖昧なものは `exact_match` にせず、`review_needed` にします。

| status | 内容 |
| --- | --- |
| `exact_match` | 名称・住所が正規化後に一致し、座標差が30m以内。1対1 |
| `review_needed` | 次のいずれか: 名称は一致するが住所が異なる／名称は一致するが住所が欠損している／名称・住所は一致するが座標差が30mを超える／住所は一致するが名称が異なる／名称は異なるが100m以内に近接している／同じ条件で一致する相手が複数ある |
| `bodik_only` | BODIK にあり、GSI 側に上のいずれの関係も見つからない |
| `gsi_only` | GSI にあり（比較対象の範囲内）、BODIK 側に上のいずれの関係も見つからない |

* 対応付けは、関係の強い順（上の表の順）、座標差の近い順に、まず1対1で行います。1対1で対応付けられなかった施設のうち、
  すでに別の施設と対応付けた相手との関係が残るもの（例: 同じ住所の「○○小学校校舎」と「○○小学校グラウンド」に対して、
  GSI は「○○小学校」1件）は、`bodik_only` / `gsi_only` にせず、相手を共有する `review_needed` の行にします（`notes` に「1対多」）。
* `bodik_only` / `gsi_only` の `notes` には、参考として最寄りの相手側施設と距離を書きます（同一施設という意味ではありません）。
* 座標間距離は `geopy.distance.geodesic` で計算します。距離の閾値（30m・100m、集計の10m・30m・100m）は目視確認の目安で、
  正誤の自動判定には使いません。

## 出力

Notebook・`compare.py` とも、実行時に次を表示します（Notebookは表で、`compare.py` はテキストで）: BODIK施設数 / GSI取得Feature数（災害種別レイヤー重複込み）/ GSIユニーク施設数 /
`exact_match`・`bodik_only`・`gsi_only`・`review_needed` の件数 / 座標差の最大・中央値・平均と10m・30m・100m超の件数 /
名称・住所の表記差 / BODIKだけ・GSIだけの施設の一覧 / `review_needed` の内訳。

`output/`（`--output-dir`）に、UTF-8（BOM付き）のCSVを出力します。

* `gsi_bodik_comparison.csv`: `status, bodik_name, bodik_address, bodik_latitude, bodik_longitude, gsi_name, gsi_address,
  gsi_latitude, gsi_longitude, distance_m, gsi_layers, notes` に加えて、正規化後の名称・住所（`*_normalized_*`）と `gsi_scope`
* `gsi_unique_shelters.csv`: レイヤー間の重複を整理したGSIの施設一覧（対象外のものも `scope` つきで含む）。元の名称・住所と
  正規化後、座標、`gsi_layers`、`disaster1`〜`disaster8`、`remarks`、`notes`

取得した公開データや生成した結果CSVは、リポジトリにコミットしません。

## テスト

* `test/test_gsi_shelter_compare.py`: `compare_logic.py` を完全な架空データで確認します（GSI内の重複施設の統合、
  `exact_match`・`bodik_only`・`gsi_only`・`review_needed`、座標距離・集計、正規化）。
* `test/test_gsi_shelter_compare_notebook.py`: Notebookを**実行せず**、ソースを静的に確認します（`compare_logic.py` を使い
  ロジックをコピーしていないこと、BODIKの resource_id が本番 `sheltermatch.ipynb` と同じこと、GSIレイヤーが
  `skhb01`〜`skhb08` であること、`SHELTER_SOURCE`・要支援者CSV・ハザードを扱わないこと、件数を固定値で埋め込んでいないこと等）。

どちらも外部通信はしません。通信を行う `compare.py` の `main()` やNotebookはテストから実行せず、CI でも実行しません。

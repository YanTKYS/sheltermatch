# 住所変換用ABRデータの取得・利用手順

この文書は、`address_geocode.ipynb`（住所→座標変換）で使用するデータについて、どこから取得し、どの
ファイルを選び、Notebookへどう投入し、結果をどう読むかをまとめた実務手順書です。実装の内部設計は
`docs/project-status.md` を参照してください。

## 1. 前提

- `address_geocode.ipynb` は、要支援者一覧CSV等の `address` 列を持つCSVへ緯度・経度を付与するための
  独立したNotebookです。**sheltermatch本体（`sheltermatch.ipynb`）には統合していません。**
- ABRデータ本体（CSV ZIP）はこのリポジトリへ保存しません。使用するたびに、公式配布元から取得して
  Notebookへアップロードします。
- 現在対応しているのは沖縄県糸満市（地方公共団体コード `472107`）の**地番住所**です。住居表示住所は、
  下記「8. geocode_status」の `residential_display_area` を参照してください。

## 2. 取得元

- **アドレス・ベース・レジストリ（ABR）公式ダウンロードサイト**（デジタル庁）
  - <https://dataset.address-br.digital.go.jp/>

非公式ミラー・GitHub上のコピー・第三者配布データは使用しないでください。

## 3. 必要なデータ（3種類）

| ファイル例 | 用途 |
| --- | --- |
| `mt_town_all.csv.zip` | 全国町字マスタ。住所から町字・`machiaza_id` を特定するために使用 |
| `mt_parcel_city472107.csv.zip` | 糸満市の地番マスタ。町字＋地番から `prc_id` を特定するために使用 |
| `mt_parcel_pos_city472107.csv.zip` | 糸満市の地番位置参照。`prc_id` に対応する代表緯度・経度を取得するために使用 |

`472107` は糸満市の地方公共団体コード（全国地方公共団体コード）です。他の市区町村を対象にする場合は、
このコードを対象自治体のものへ読み替えて取得してください。

## 4. 現在使わないデータ

`abr_post_code.zip` 等の郵便番号データは、現在の `address_geocode.ipynb` の住所→座標変換では使用しま
せん。取得済みであっても問題はなく、Notebookは列構成から郵便番号データと認識した場合は無視する仕様です。

## 5. ダウンロード手順

1. ABR公式ダウンロードサイト（<https://dataset.address-br.digital.go.jp/>）を開く
2. 検索窓で「町字」等のキーワードや都道府県名で検索し、**全国町字マスタ**のデータセットを開いて
   `mt_town_all.csv.zip` 相当のファイルを取得する
3. 同様に検索し、**地番マスタ**のデータセットから沖縄県・糸満市（`472107`）分のZIPを取得する
4. **地番位置参照（位置参照拡張）**のデータセットからも、同じく糸満市（`472107`）分のZIPを取得する
5. いずれのZIPも展開せず、そのまま保存する
6. `address_geocode.ipynb` の「ABRマスター準備」セルで、この3ZIPをまとめてアップロードする

公式サイトの画面構成・メニュー名は変更される可能性があります。上記はキーワード検索から対象データの
詳細ページを開き、ダウンロードするという大まかな流れとして参照してください。

## 6. Notebook側の扱い

- ZIPは展開せず、そのままアップロードします。
- 3ZIPを1回のアップロードでまとめて選択できます。
- ファイル名ではなく、ZIP内CSVの列構成（`lg_code`/`machiaza_id`等）からデータ種別を自動判定します。
- 全国町字マスタからは `lg_code=472107` のデータだけを抽出します。地番マスタ・地番位置参照も同じ
  糸満市データを使用します。
- 「ABRマスター準備」セルを一度実行すれば、同じColabランタイムが生きている間は、住所CSVを差し替える
  たびにABR ZIPを再アップロードする必要はありません。「住所CSV変換・出力」セルだけを繰り返し
  再実行できます。

## 7. 共通住民CSVとの関係

住所データ（共通住民CSV）とABRマスタ（上記3ZIP）は別のものです。

`address_geocode.ipynb` は、次の5列を持つ**共通住民CSV**を入力・出力とします（テンプレート:
[templates/residents.csv](../templates/residents.csv)）。

```text
resident_id,address,latitude,longitude,geocode_status
```

- 初回投入時に値が必要なのは `resident_id` と `address` だけです。`latitude` / `longitude` /
  `geocode_status` は空欄で構いません。
- `resident_id` は住民を一意に識別するIDです。空欄・重複がある場合、`address_geocode.ipynb` は
  どの行に問題があるかを示した上で処理を止めます（住所変換結果と避難所候補算出結果を紐づける
  結合キーのため）。
- 変換後も列構成は変わりません。`latitude` / `longitude` / `geocode_status` の3列が、変換のたびに
  最新の結果で埋まる・上書きされるだけです。`normalized_address` 等のABR内部確認用の列は
  Notebook上の確認表示にのみ使用し、出力CSVには含めません。

列構成が変換前後で変わらないため、出力CSVは加工せずに次のどちらにも使えます。

- 住所を修正して、同じCSVを再度 `address_geocode.ipynb` へ投入する（再変換。修正した行だけでなく
  全行を再評価しても問題ありません。古い `latitude` / `longitude` / `geocode_status` は再変換の
  たびに最新の結果へ置き換わります）
- そのまま `sheltermatch.ipynb` の要支援者CSVとしてアップロードする（sheltermatch側は
  `latitude` / `longitude` を必須とし、`geocode_status` を含むそれ以外の列はそのまま結果へ保持する
  仕様です）

## 8. geocode_status

変換できなかった行も削除せず、`geocode_status` 列で状態を確認できます。曖昧な住所を推測して
`matched` 扱いにすることはありません。

| geocode_status | 意味 |
| --- | --- |
| `matched` | 町字・地番が一意に特定でき、座標を取得できた |
| `blank_address` | `address` が空欄だった |
| `town_not_found` | 入力住所の先頭と一致する町字が見つからなかった |
| `ambiguous_town` | 同じ長さで複数の町字（`machiaza_id`）に一致し、一つに絞れなかった |
| `parcel_not_found` | 町字は特定できたが、該当する地番が地番マスタに見つからなかった |
| `ambiguous_parcel` | 地番の候補が複数残り、一つに絞れなかった |
| `coordinates_missing` | 地番は特定できたが、対応する位置参照データに座標が無かった |
| `residential_display_area` | 該当町字が住居表示地域だった |

`residential_display_area` は**エラーではありません**。現在使用している町字・地番・地番位置参照の
3データだけでは住居表示住所を正しく座標化できるとは限らないため、地番として無理に確定させず、座標化を
明示的に保留している状態です。該当件数が多い場合は、住居表示・街区／住居表示・住居データの追加が
必要かどうかを判断してください。

## 9. データの更新・保管方針

- ABR公式CSV ZIPそのものは、このリポジトリへコミットしません。必要になった時点で公式配布元から
  取得してください。
- ABRデータが更新された場合は、その時点の最新の公式データを取得し直してください。過去に取得したZIPを
  プロジェクトの正本として扱いません。
- 実際の住所CSV・変換済みCSV等、個人情報を含み得るデータは、いかなる形式でもコミットしません。
- `experiments/address_conversion_test_input.csv` は、個人情報を含まない検証用サンプルとして
  リポジトリ内に置いています。

## 10. 実機確認済み事項

`experiments/address_conversion_test_input.csv`（8件）を実際のABRデータで変換し、次の結果になる
ことを確認済みです。

```text
8行中6行 matched
1行 town_not_found
1行 blank_address
```

また、以下の表記の違いがあっても同じ地番・座標へ一致することを確認済みです。

- 通常住所（都道府県・市名を含む表記）
- 都道府県名の省略
- 全角数字での丁目・地番表記
- 「番地」表記の省略

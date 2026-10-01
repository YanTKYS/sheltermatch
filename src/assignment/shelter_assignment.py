"""sheltermatch 避難所候補の算出・本人住所のハザード判定・ハザード別人数の集計・結果CSVの組み立て。

sheltermatch.ipynb はGoogle Colabでの実行時にこのファイルをGitHubから取得し、
`build_assignment()` と `summarize_hazards()` を呼び出すだけにしています。Notebookのグローバル変数には
依存せず、必要なデータはすべて引数で受け取ります。

担当部署の業務要件に合わせて、行うことは次の3つだけです。

1. 避難所候補1〜3を、本人の地点から避難所までの直線距離（`geopy.distance.geodesic`）だけで決める
   （距離が同じ場合は避難所名で順序を安定させる。避難所のハザード・災害種別・道路距離等では変えない）
2. 要支援者本人の地点が、ハザード区域の内部または境界上にあるかを判定する（避難所の地点や、
   本人と避難所を結ぶ直線・道路経路についてはハザード判定をしない）
3. ハザードの大分類ごとに、本人の地点が該当する人数を集計する（人 × 大分類で重複を除く）

座標が使えない人（`no_coordinates` / `invalid_coordinates`）は、行を削除せず、候補・距離・ハザード判定を
すべて空欄にします。曖昧な座標・近い住所・代表点による補完はしません。
"""
import re

import numpy as np
import pandas as pd
from geopy.distance import geodesic
from shapely.geometry import Point

# Notebookとこのモジュールの受け渡し方（build_assignment・summarize_hazardsの引数・戻り値）を
# 変えたら上げる。Notebook側は読み込んだ直後にこの値を確認し、互換性のない組み合わせのまま処理を続けない。
ASSIGNMENT_API_VERSION = 1

# 候補避難所の件数。正式仕様として3件固定（利用者設定では変えられない）。
CANDIDATE_COUNT = 3

# 正式な結果CSV（assigned_shelters.csv）の列。列順もこのとおり。
RESIDENT_COLUMNS = ["resident_id", "address", "latitude", "longitude", "geocode_status"]
HAZARD_COLUMNS = ["resident_in_hazard", "resident_hazard_types"]
CANDIDATE_COLUMNS = [
    column
    for n in range(1, CANDIDATE_COUNT + 1)
    for column in (f"candidate_{n}", f"distance_{n}_m")
]
RESULT_COLUMNS = [*RESIDENT_COLUMNS, "match_status", *HAZARD_COLUMNS, *CANDIDATE_COLUMNS]

# このツールが結果として作る列名（入力CSVに前回の結果列が含まれていた場合に、今回の結果で置き換える対象）。
# 旧版（v1.0.0）が作っていた列（避難所の災害種別対応・避難所地点のハザード・直線交差のハザード・
# TOP_Nを増やした場合の candidate_4 以降）も、同じく前回の結果列として取り除く。
# 取り除くのは、ここに挙げた名前と完全に一致する列だけで、candidate_1_備考 のように職員が独自に
# 追加した列は、名前が似ていても取り除かない（入力されたデータを黙って消さないため）。
GENERATED_COLUMNS = ("match_status", *HAZARD_COLUMNS)
GENERATED_CANDIDATE_COLUMN_TEMPLATES = (
    "candidate_{n}",
    "distance_{n}_m",
    "candidate_{n}_disaster_support",
    "candidate_{n}_shelter_in_hazard",
    "candidate_{n}_shelter_hazard_types",
    "candidate_{n}_straight_line_intersects_hazard",
    "candidate_{n}_straight_line_hazard_types",
)

# ハザード別の人数集計で、大分類を並べる順（これ以外の大分類は、この後ろに名前順で並べる）。
HAZARD_CATEGORY_ORDER = ["津波", "高潮", "洪水", "土砂災害"]


def sort_hazard_categories(categories):
    """ハザードの大分類を、HAZARD_CATEGORY_ORDER の順、それ以外は名前順に並べて返す（重複は除く）。"""
    unique = sorted(set(categories))
    known = [category for category in HAZARD_CATEGORY_ORDER if category in unique]
    return known + [category for category in unique if category not in known]


# =============================================================================
# 避難所候補（直線距離だけで決める）
# =============================================================================


def build_shelter_records(shelters_df):
    """避難所DataFrameから、距離計算に使う項目（名称・緯度・経度）だけを取り出したタプルのリストを作る。
    要支援者1人ごとにDataFrameを1行ずつ取り出し直すと人数分だけ無駄に時間がかかるため、
    最初に1回だけ作って全員で使い回す。名前は並び順を安定させるため文字列に揃える。"""
    return [
        (str(name), latitude, longitude)
        for name, latitude, longitude in zip(
            shelters_df["name"], shelters_df["latitude"], shelters_df["longitude"]
        )
    ]


def compute_candidates(resident_lat, resident_lon, shelter_records, count=CANDIDATE_COUNT):
    """要支援者の座標から近い順に避難所候補を [(名前, 距離m, 緯度, 経度), ...] で返す。
    距離は丸めずに返す（並び替え後、出力時にのみ丸める）。距離が同一の場合は避難所名で順序を
    安定させる。順位は直線距離だけで決まり、ハザード・災害種別・道路距離等は使わない。"""
    resident_coord = (resident_lat, resident_lon)
    candidates = [
        (name, geodesic(resident_coord, (lat, lon)).meters, lat, lon)
        for name, lat, lon in shelter_records
    ]
    candidates.sort(key=lambda candidate: (candidate[1], candidate[0]))
    return candidates[:count]


# =============================================================================
# 本人住所のハザード判定
# =============================================================================


def resident_hazards(lat, lon, hazard_area):
    """本人の地点がハザード区域の内部または境界上にあるかを判定し、該当した区域を大分類ごとに
    まとめた一覧 [{"category": 大分類, "types": [詳細区分つきのhazard_type, ...]}, ...] を返す
    （大分類は sort_hazard_categories の順、typesは名前順。該当なしは空の一覧）。

    ハザード区域は10万件規模になるため、1回の判定ごとに全ポリゴンを調べると要支援者数に比例して
    待ち時間が延びる。GeoPandasの空間インデックス（hazard_area.sindex。1度作れば以降は再利用
    される）で交差し得るポリゴンだけに絞ってから判定する（判定結果は絞り込みの有無で変わらない）。"""
    hit_rows = hazard_area.sindex.query(Point(lon, lat), predicate="intersects")
    hits = hazard_area.iloc[hit_rows]
    hazards = []
    for category in sort_hazard_categories(hits["hazard_category"]):
        types = sorted(hits.loc[hits["hazard_category"] == category, "hazard_type"].unique())
        hazards.append({"category": category, "types": types})
    return hazards


def hazard_types_text(hazards):
    """resident_hazard_types 列に出力する文字列（詳細区分つきのhazard_typeを ';' で連結）。"""
    return ";".join(sorted({hazard_type for hazard in hazards for hazard_type in hazard["types"]}))


# =============================================================================
# ハザード別人数の集計
# =============================================================================


def summarize_hazards(review_rows, hazard_categories, hazard_checked):
    """本人住所のハザード判定結果を、ハザードの大分類ごとの人数へ集計する。

    review_rows        build_assignment が返す、要支援者ごとの表示用データ（"has_coordinates" と
                       "hazards" を持つ。座標が使えない人の "hazards" は None）
    hazard_categories  読み込んだハザードデータが持つ大分類の一覧（該当者が0人の大分類も
                       「0人」として載せるため、データから決める。この一覧に無い大分類に該当した人がいれば
                       それも載せる）
    hazard_checked     ハザード判定を行ったか（行っていない場合、ハザードの集計は None にする）

    数え方（人 × 大分類で重複を除く。表示用の文字列は解析せず、判定時に付けた大分類を使う）:
      * 同じ人が同じ大分類の複数ポリゴンに該当しても、その大分類は1人
      * 同じ人が複数の大分類に該当した場合は、該当した大分類それぞれに1人ずつ計上する
      * any_hazard（いずれかに該当）は、1つ以上の大分類に該当した人を、重複なしで数える
        （各大分類の人数を合計した値とは一致しない場合がある）
      * no_hazard（ハザード該当なし）は、有効な座標があり、判定した結果どの大分類にも該当しなかった人
      * without_coordinates（座標未取得）は、座標が使えず判定できなかった人。ハザード該当なしには含めない
    """
    total = len(review_rows)
    with_coordinates = sum(1 for row in review_rows if row["has_coordinates"])
    summary = {
        "total": total,
        "with_coordinates": with_coordinates,
        "without_coordinates": total - with_coordinates,
        "hazard_checked": bool(hazard_checked),
        "any_hazard": None,
        "no_hazard": None,
        "by_category": None,
    }
    if not hazard_checked:
        return summary

    people_by_category = {}
    any_hazard = 0
    for row in review_rows:
        if not row["has_coordinates"]:
            continue
        categories = {hazard["category"] for hazard in row["hazards"]}
        if categories:
            any_hazard += 1
        for category in categories:
            people_by_category[category] = people_by_category.get(category, 0) + 1

    ordered = sort_hazard_categories([*hazard_categories, *people_by_category])
    summary["any_hazard"] = any_hazard
    summary["no_hazard"] = with_coordinates - any_hazard
    summary["by_category"] = [
        {"category": category, "count": people_by_category.get(category, 0)} for category in ordered
    ]
    return summary


# =============================================================================
# 結果の組み立て
# =============================================================================


def is_generated_column(column_name):
    """このツールが結果として作る列名（旧版が作っていた列を含む）かどうかを返す
    （上の一覧と完全に一致する名前のみTrue）。候補番号(N)は数字として判定する。"""
    if column_name in GENERATED_COLUMNS:
        return True
    return any(
        re.fullmatch(template.format(n=r"\d+"), column_name)
        for template in GENERATED_CANDIDATE_COLUMN_TEMPLATES
    )


def build_assignment(residents, resident_lat, resident_lon, resident_coord_status,
                     shelter_records, hazard_area=None):
    """要支援者ごとに、避難所候補1〜3・距離・本人住所のハザード判定を求め、正式な結果CSVの
    DataFrameと、レビュー用HTMLへ渡す表示用データを返す。

    residents               入力した要支援者CSVのDataFrame（resident_id・address・latitude・longitude・
                            geocode_status の5列が必須。入力した値はそのまま結果CSVへ残す）
    resident_lat/lon        数値化した緯度・経度（座標が使えない行はNaN）
    resident_coord_status   行ごとの座標の状態（ok / no_coordinates / invalid_coordinates）
    shelter_records         build_shelter_records の戻り値
    hazard_area             ハザード区域のGeoDataFrame（hazard_category・hazard_type・geometry）。
                            ハザード判定を行わない場合はNone（ハザードの列は空欄になる）

    戻り値は (final_df, review_rows, replaced_columns)。
      final_df          正式な14列（RESULT_COLUMNS）をこの順に並べ、入力CSVにほかの列があればその後ろに
                        そのまま付ける（入力されたデータを黙って消さない）
      review_rows       要支援者ごとの表示用データ。候補の順位・距離は、結果CSVと同じ算出結果をそのまま使う
      replaced_columns  入力CSVに前回の結果列が含まれていたため、今回の結果で置き換えた列名の一覧
    """
    missing = [column for column in RESIDENT_COLUMNS if column not in residents.columns]
    if missing:
        raise ValueError(f"要支援者一覧に必須列がありません: {', '.join(missing)}")

    result_records = []
    review_rows = []

    for lat, lon, coord_status in zip(resident_lat, resident_lon, resident_coord_status):
        has_coordinates = coord_status == "ok"
        record = {"match_status": coord_status}

        # 座標が空欄・不正な要支援者は、候補を算出せず、ハザードも判定しない
        # （空欄は「判定できなかった」であり、「ハザード区域外」ではない）。
        hazards = None
        in_hazard = hazard_text = np.nan
        if has_coordinates and hazard_area is not None:
            hazards = resident_hazards(lat, lon, hazard_area)
            in_hazard, hazard_text = len(hazards) > 0, hazard_types_text(hazards)
        record["resident_in_hazard"] = in_hazard
        record["resident_hazard_types"] = hazard_text

        candidates = compute_candidates(lat, lon, shelter_records) if has_coordinates else []
        review_candidates = []
        for n in range(1, CANDIDATE_COUNT + 1):
            name = distance_m = np.nan
            if n <= len(candidates):
                name, distance_raw, shelter_lat, shelter_lon = candidates[n - 1]
                distance_m = round(distance_raw, 1)
                review_candidates.append({
                    "rank": n,
                    "name": name,
                    "latitude": shelter_lat,
                    "longitude": shelter_lon,
                    "distance_m": distance_m,
                })
            record[f"candidate_{n}"] = name
            record[f"distance_{n}_m"] = distance_m

        result_records.append(record)
        review_rows.append({
            "has_coordinates": has_coordinates,
            "latitude": lat if has_coordinates else None,
            "longitude": lon if has_coordinates else None,
            "hazards": hazards,
            "candidates": review_candidates,
        })

    results_df = pd.DataFrame(result_records, columns=[
        "match_status", *HAZARD_COLUMNS, *CANDIDATE_COLUMNS,
    ])

    # 前回の結果CSV（assigned_shelters.csv）をそのまま再投入された場合に、同名の列が二重になって
    # 以降の処理が落ちないよう、このツールが作る列は入力側から取り除いてから連結する。
    reused_columns = [c for c in residents.columns if is_generated_column(c)]
    inputs = residents.drop(columns=reused_columns).reset_index(drop=True)
    extra_columns = [c for c in inputs.columns if c not in RESIDENT_COLUMNS]

    final_df = pd.concat([inputs[RESIDENT_COLUMNS], results_df, inputs[extra_columns]], axis=1)
    final_df = final_df[[*RESULT_COLUMNS, *extra_columns]]
    return final_df, review_rows, reused_columns

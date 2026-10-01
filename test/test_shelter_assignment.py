"""避難所候補・本人住所のハザード判定・ハザード大分類別の人数集計・結果CSVの回帰テスト
（src/assignment/shelter_assignment.py）。完全な架空データだけを使い、外部通信は行わない。

担当部署の業務要件（v1.1.0）:
  * 候補は本人住所→避難所の直線距離（geodesic）だけで決める（ハザード・災害種別では変えない）
  * ハザード判定は本人の地点だけ（避難所の地点・直線との交差は判定しない）
  * ハザードの大分類ごとに、人 × 大分類で重複を除いて人数を集計する
  * 座標が使えない行は削除せず、候補・距離・ハザードを空欄にし、「ハザード該当なし」に含めない
  * 正式なCSVは14列

    python3 -m unittest discover -s test
"""
import io
import unittest
from unittest import mock

import geopandas as gpd
import numpy as np
import pandas as pd
from geopy.distance import geodesic
from shapely.geometry import box

import fake_data
from notebook_harness import load_src_module

assignment = load_src_module("assignment/shelter_assignment.py", "shelter_assignment")

EXPECTED_COLUMNS = [
    "resident_id", "address", "latitude", "longitude", "geocode_status",
    "match_status",
    "resident_in_hazard", "resident_hazard_types",
    "candidate_1", "distance_1_m", "candidate_2", "distance_2_m", "candidate_3", "distance_3_m",
]


def fake_hazard_area():
    """fake_data の区域を、hazard_loader が返すのと同じ列（hazard_category / hazard_type / geometry）で持つ。"""
    rows = [
        ("津波", "津波:0.01m以上0.3m未満", fake_data.TSUNAMI_LOW),
        ("津波", "津波:0.3m以上1.0m未満", fake_data.TSUNAMI_HIGH),
        ("高潮", "高潮", fake_data.STORM_SURGE),
        ("洪水", "洪水（その他の河川）", fake_data.FLOOD),
        ("土砂災害", "土砂災害:急傾斜地の崩壊:土砂災害警戒区域(指定済)", fake_data.LANDSLIDE),
    ]
    return gpd.GeoDataFrame(
        {
            "hazard_category": [r[0] for r in rows],
            "hazard_type": [r[1] for r in rows],
            "geometry": [box(*r[2]) for r in rows],
        },
        crs="EPSG:4326",
    )


def fake_shelters_df(with_disaster_columns=False):
    shelters = pd.DataFrame({
        "name": [s["名称"] for s in fake_data.SHELTERS],
        "latitude": [float(s["緯度"]) for s in fake_data.SHELTERS],
        "longitude": [float(s["経度"]) for s in fake_data.SHELTERS],
    })
    if with_disaster_columns:
        shelters["災害種別_洪水"] = [s["災害種別_洪水"] for s in fake_data.SHELTERS]
        shelters["災害種別_津波"] = [s["災害種別_津波"] for s in fake_data.SHELTERS]
    return shelters


def read_residents(csv_bytes=None):
    """Notebook の要支援者CSV読込と同じ方法（resident_idだけ文字列。座標の状態は read_coordinates と同じ規則）。"""
    df = pd.read_csv(io.BytesIO(csv_bytes or fake_data.residents_csv_bytes()), dtype={"resident_id": str})
    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    blank = lambda s: s.isna() | (s.astype(str).str.strip() == "")  # noqa: E731
    status = pd.Series("ok", index=df.index)
    status[~(lat.between(-90, 90) & lon.between(-180, 180))] = "invalid_coordinates"
    status[blank(df["latitude"]) | blank(df["longitude"])] = "no_coordinates"
    return df, lat, lon, status


def run_assignment(hazard_area="default", shelters_df=None, csv_bytes=None):
    residents, lat, lon, status = read_residents(csv_bytes)
    area = fake_hazard_area() if isinstance(hazard_area, str) else hazard_area
    records = assignment.build_shelter_records(shelters_df if shelters_df is not None else fake_shelters_df())
    return assignment.build_assignment(residents, lat, lon, status, records, area)


class ResultColumnsTest(unittest.TestCase):
    def test_正式な出力列は14列(self):
        self.assertEqual(assignment.RESULT_COLUMNS, EXPECTED_COLUMNS)
        self.assertEqual(len(assignment.RESULT_COLUMNS), 14)

    def test_結果は14列をこの順に並べ_入力の独自列はその後ろへ残す(self):
        final_df, _, _ = run_assignment()
        self.assertEqual(list(final_df.columns), EXPECTED_COLUMNS + ["note"])
        self.assertEqual(list(final_df["note"]), [r[5] for r in fake_data.RESIDENTS])

    def test_独自列が無ければ正式な14列だけ(self):
        final_df, _, _ = run_assignment(csv_bytes=fake_data.residents_csv_bytes(extra_header=None))
        self.assertEqual(list(final_df.columns), EXPECTED_COLUMNS)

    def test_ハザード判定を行わない場合も14列で_ハザード列は空欄(self):
        final_df, _, _ = run_assignment(hazard_area=None)
        self.assertEqual(list(final_df.columns[:14]), EXPECTED_COLUMNS)
        self.assertTrue(final_df["resident_in_hazard"].isna().all())
        self.assertTrue(final_df["resident_hazard_types"].isna().all())

    def test_旧版の列は出さない(self):
        for hazard_area in ("default", None):
            final_df, _, _ = run_assignment(hazard_area=hazard_area, shelters_df=fake_shelters_df(True))
            for column in final_df.columns:
                self.assertNotIn("disaster_support", column)          # 避難所の災害種別対応
                self.assertNotIn("shelter_in_hazard", column)         # 避難所地点のハザード
                self.assertNotIn("shelter_hazard_types", column)
                self.assertNotIn("straight_line", column)             # 本人→避難所の直線の交差ハザード
            self.assertFalse(final_df.columns.str.startswith("災害種別").any())

    def test_前回の結果列は置き換え_旧版の列も取り除き_職員が追加した列は残す(self):
        residents, lat, lon, status = read_residents()
        residents["match_status"] = "古い値"
        residents["candidate_1"] = "古い候補"
        residents["candidate_2_disaster_support"] = "洪水:対応済み"                 # 旧版の列
        residents["candidate_3_shelter_in_hazard"] = True                          # 旧版の列
        residents["candidate_1_straight_line_hazard_types"] = "津波"                # 旧版の列
        residents["candidate_4"] = "TOP_Nを増やしていた前回の列"
        residents["distance_4_m"] = 1.0
        residents["candidate_1_備考"] = "職員の追加列"
        records = assignment.build_shelter_records(fake_shelters_df())
        final_df, _, replaced = assignment.build_assignment(
            residents, lat, lon, status, records, fake_hazard_area()
        )
        self.assertEqual(
            sorted(replaced),
            sorted(["match_status", "candidate_1", "candidate_2_disaster_support",
                    "candidate_3_shelter_in_hazard", "candidate_1_straight_line_hazard_types",
                    "candidate_4", "distance_4_m"]),
        )
        self.assertEqual(list(final_df.columns), EXPECTED_COLUMNS + ["note", "candidate_1_備考"])
        self.assertNotIn("古い候補", set(final_df["candidate_1"].dropna()))
        self.assertEqual(list(final_df["candidate_1_備考"]), ["職員の追加列"] * len(fake_data.RESIDENTS))

    def test_入力の列はそのまま残る(self):
        final_df, _, _ = run_assignment()
        for position, (resident_id, address, latitude, longitude, status, _) in enumerate(fake_data.RESIDENTS):
            row = final_df.iloc[position]
            self.assertEqual(row["resident_id"], resident_id)
            self.assertEqual(row["address"], address)
            self.assertEqual(row["geocode_status"], status)
        # 数値として読めない座標も、入力した値のまま残る
        self.assertEqual(str(final_df.loc[final_df["resident_id"] == "R005", "latitude"].iloc[0]), "不明")


class CandidateRankingTest(unittest.TestCase):
    def expected_order(self, lat, lon, shelters_df):
        scored = [
            (geodesic((lat, lon), (s.latitude, s.longitude)).meters, s.name)
            for s in shelters_df.itertuples()
        ]
        return sorted(scored)[:3]

    def test_候補1から3は直線距離順で_距離は小数1桁(self):
        shelters = fake_shelters_df()
        final_df, review_rows, _ = run_assignment()
        for position, resident in enumerate(fake_data.RESIDENTS):
            row = final_df.iloc[position]
            if row["match_status"] != "ok":
                continue
            expected = self.expected_order(float(resident[2]), float(resident[3]), shelters)
            for n, (distance, name) in enumerate(expected, start=1):
                self.assertEqual(row[f"candidate_{n}"], name)
                self.assertEqual(row[f"distance_{n}_m"], round(distance, 1))
            self.assertEqual([c["name"] for c in review_rows[position]["candidates"]],
                             [name for _, name in expected])

    def test_候補は3件固定(self):
        self.assertEqual(assignment.CANDIDATE_COUNT, 3)
        final_df, review_rows, _ = run_assignment()
        self.assertNotIn("candidate_4", final_df.columns)
        self.assertTrue(all(len(row["candidates"]) <= 3 for row in review_rows))

    def test_避難所が3件未満なら存在する件数まで_残りは空欄(self):
        final_df, _, _ = run_assignment(shelters_df=fake_shelters_df().iloc[:2])
        row = final_df[final_df["resident_id"] == "R001"].iloc[0]
        self.assertTrue(pd.notna(row["candidate_2"]))
        self.assertTrue(pd.isna(row["candidate_3"]) and pd.isna(row["distance_3_m"]))

    def test_距離が同じ場合は避難所名で順序が安定する(self):
        # 同じ座標の避難所を、名前の順序と逆に並べて渡しても、名前順になる
        shelters = pd.DataFrame({
            "name": ["ゆ避難所", "あ避難所", "い避難所"],
            "latitude": [26.13, 26.13, 26.13], "longitude": [127.67, 127.67, 127.67],
        })
        candidates = assignment.compute_candidates(
            26.12, 127.66, assignment.build_shelter_records(shelters))
        self.assertEqual([c[0] for c in candidates], ["あ避難所", "い避難所", "ゆ避難所"])
        self.assertEqual(len({c[1] for c in candidates}), 1)

    def test_ハザードの有無や避難所のハザードで順位が変わらない(self):
        without_hazard, _, _ = run_assignment(hazard_area=None)
        with_hazard, _, _ = run_assignment()
        candidate_columns = assignment.CANDIDATE_COLUMNS
        pd.testing.assert_frame_equal(without_hazard[candidate_columns], with_hazard[candidate_columns])

        # 最も近い避難所（架空避難所A）の地点を含む区域を足しても、その避難所は候補1のまま
        covering = fake_hazard_area()
        extra = gpd.GeoDataFrame(
            {"hazard_category": ["津波"], "hazard_type": ["津波:大きな区域"],
             "geometry": [box(127.0, 26.0, 128.0, 27.0)]}, crs="EPSG:4326")
        covering = pd.concat([covering, extra], ignore_index=True)
        covered, _, _ = run_assignment(hazard_area=gpd.GeoDataFrame(covering, crs="EPSG:4326"))
        pd.testing.assert_frame_equal(without_hazard[candidate_columns], covered[candidate_columns])
        r001 = covered[covered["resident_id"] == "R001"].iloc[0]
        self.assertEqual(r001["candidate_1"], "架空避難所A")

    def test_避難所の災害種別列があっても順位は変わらない(self):
        plain, _, _ = run_assignment()
        with_columns, _, _ = run_assignment(shelters_df=fake_shelters_df(True))
        pd.testing.assert_frame_equal(plain, with_columns)
        # 候補の算出に使うのは名称・緯度・経度だけ
        records = assignment.build_shelter_records(fake_shelters_df(True))
        self.assertTrue(all(len(record) == 3 for record in records))

    def test_ハザードの判定は候補の順位を決める関数へ渡されない(self):
        import inspect
        self.assertEqual(
            list(inspect.signature(assignment.compute_candidates).parameters),
            ["resident_lat", "resident_lon", "shelter_records", "count"],
        )


class ResidentHazardTest(unittest.TestCase):
    def test_判定の対象は本人の地点だけ(self):
        hazard_area = fake_hazard_area()
        real_index = hazard_area.sindex
        queried = []

        class SpyIndex:
            def query(self, geometry, predicate=None):
                queried.append((geometry.geom_type, geometry.x, geometry.y, predicate))
                return real_index.query(geometry, predicate=predicate)

        residents, lat, lon, status = read_residents()
        records = assignment.build_shelter_records(fake_shelters_df())
        with mock.patch.object(gpd.GeoDataFrame, "sindex", new_callable=mock.PropertyMock) as sindex:
            sindex.return_value = SpyIndex()
            assignment.build_assignment(residents, lat, lon, status, records, hazard_area)

        # 空間判定の対象は、有効な座標を持つ本人の地点（点）だけ。避難所の点や直線は判定していない
        ok_positions = [i for i, s in enumerate(status) if s == "ok"]
        self.assertEqual(len(queried), len(ok_positions))
        self.assertEqual({q[0] for q in queried}, {"Point"})
        self.assertEqual(
            sorted((q[1], q[2]) for q in queried),
            sorted((lon[i], lat[i]) for i in ok_positions),
        )
        self.assertEqual({q[3] for q in queried}, {"intersects"})
        self.assertFalse(hasattr(assignment, "hazard_types_on_line"))
        self.assertFalse(hasattr(assignment, "shelter_hazard_at"))

    def test_本人のハザードの判定結果(self):
        final_df, review_rows, _ = run_assignment()
        by_id = {row["resident_id"]: (row, review_rows[i]) for i, (_, row) in enumerate(final_df.iterrows())}

        row, review = by_id["R001"]
        self.assertTrue(row["resident_in_hazard"])
        self.assertEqual(
            row["resident_hazard_types"],
            "津波:0.01m以上0.3m未満;津波:0.3m以上1.0m未満;高潮",
        )
        self.assertEqual([h["category"] for h in review["hazards"]], ["津波", "高潮"])
        self.assertEqual(review["hazards"][0]["types"], ["津波:0.01m以上0.3m未満", "津波:0.3m以上1.0m未満"])

        row, _ = by_id["R002"]
        self.assertTrue(row["resident_in_hazard"])
        self.assertEqual(row["resident_hazard_types"], "洪水（その他の河川）")

        row, review = by_id["R003"]
        self.assertFalse(row["resident_in_hazard"])
        self.assertEqual(row["resident_hazard_types"], "")
        self.assertEqual(review["hazards"], [])

    def test_座標が使えない行は削除せず_ハザード結果と候補は空欄(self):
        final_df, review_rows, _ = run_assignment()
        self.assertEqual(len(final_df), len(fake_data.RESIDENTS))
        expected_status = {"R004": "no_coordinates", "R005": "invalid_coordinates"}
        for resident_id, status in expected_status.items():
            position = [r[0] for r in fake_data.RESIDENTS].index(resident_id)
            row = final_df.iloc[position]
            self.assertEqual(row["resident_id"], resident_id)
            self.assertEqual(row["address"], fake_data.RESIDENTS[position][1])
            self.assertEqual(row["match_status"], status)
            for column in ["resident_in_hazard", "resident_hazard_types", *assignment.CANDIDATE_COLUMNS]:
                self.assertTrue(pd.isna(row[column]), f"{resident_id}の{column}が空欄ではありません")
            self.assertIsNone(review_rows[position]["hazards"])
            self.assertEqual(review_rows[position]["candidates"], [])

    def test_ハザード判定を行わない場合は判定しない(self):
        _, review_rows, _ = run_assignment(hazard_area=None)
        self.assertTrue(all(row["hazards"] is None for row in review_rows))


class HazardSummaryTest(unittest.TestCase):
    def summary(self, hazard_area="default", **overrides):
        _, review_rows, _ = run_assignment(hazard_area=hazard_area)
        area = fake_hazard_area() if isinstance(hazard_area, str) else hazard_area
        categories = sorted(set(area["hazard_category"])) if area is not None else []
        return assignment.summarize_hazards(review_rows, categories, area is not None)

    def counts(self, summary):
        return {item["category"]: item["count"] for item in summary["by_category"]}

    def test_件数と大分類別の人数(self):
        summary = self.summary()
        self.assertEqual(summary["total"], 7)
        self.assertEqual(summary["with_coordinates"], 5)
        self.assertEqual(summary["without_coordinates"], 2)
        self.assertEqual(summary["any_hazard"], 4)
        self.assertEqual(summary["no_hazard"], 1)
        self.assertEqual(self.counts(summary), {"津波": 2, "高潮": 1, "洪水": 2, "土砂災害": 1})
        self.assertEqual([item["category"] for item in summary["by_category"]],
                         ["津波", "高潮", "洪水", "土砂災害"])

    def test_同じ大分類の複数ポリゴンに該当しても1人(self):
        # R001 は津波の2ポリゴンに該当するが、津波は R001・R006 の2人（延べ3件ではない）
        self.assertEqual(self.counts(self.summary())["津波"], 2)

    def test_複数の大分類に該当する人は各分類に1人ずつ_いずれかに該当は重複なし(self):
        summary = self.summary()
        by_category = self.counts(summary)
        # R001（津波＋高潮）・R007（洪水＋土砂災害）は、それぞれの大分類に1人ずつ計上される
        self.assertEqual(sum(by_category.values()), 6)
        # いずれかに該当は人物単位で重複を除くため、各分類の合計（6）とは一致しない
        self.assertEqual(summary["any_hazard"], 4)
        self.assertNotEqual(sum(by_category.values()), summary["any_hazard"])

    def test_ハザード該当なしは有効な座標で判定して該当が無かった人だけ(self):
        summary = self.summary()
        self.assertEqual(summary["no_hazard"], 1)       # R003 だけ
        # 座標未取得（no_coordinates / invalid_coordinates）は「ハザード該当なし」に含めない
        self.assertEqual(summary["any_hazard"] + summary["no_hazard"], summary["with_coordinates"])
        self.assertEqual(summary["with_coordinates"] + summary["without_coordinates"], summary["total"])

    def test_集計は判定時に付けた大分類を使い_表示用の文字列は解析しない(self):
        # 詳細区分の文字列に別の大分類名を含んでいても、大分類（hazard_category）どおりに数える
        area = gpd.GeoDataFrame(
            {"hazard_category": ["津波"], "hazard_type": ["高潮:洪水:土砂災害"],
             "geometry": [box(*fake_data.TSUNAMI_LOW)]}, crs="EPSG:4326")
        summary = self.summary(hazard_area=area)
        self.assertEqual(self.counts(summary), {"津波": 2})  # R001・R006

    def test_該当者が0人の大分類も_読み込んだデータにあれば0人として載せる(self):
        area = fake_hazard_area()
        area.loc[area["hazard_category"] == "高潮", "geometry"] = box(0, 0, 0.1, 0.1)
        summary = self.summary(hazard_area=area)
        self.assertEqual(self.counts(summary)["高潮"], 0)

    def test_既知以外の大分類も集計し_既知の後ろへ名前順に並ぶ(self):
        area = pd.concat([fake_hazard_area(), gpd.GeoDataFrame(
            {"hazard_category": ["河川氾濫"], "hazard_type": ["河川氾濫"], "geometry": [box(*fake_data.FLOOD)]},
            crs="EPSG:4326")], ignore_index=True)
        summary = self.summary(hazard_area=gpd.GeoDataFrame(area, crs="EPSG:4326"))
        self.assertEqual([item["category"] for item in summary["by_category"]],
                         ["津波", "高潮", "洪水", "土砂災害", "河川氾濫"])
        self.assertEqual(self.counts(summary)["河川氾濫"], 2)

    def test_ハザード判定を行わない場合はハザードの集計を出さない(self):
        summary = self.summary(hazard_area=None)
        self.assertFalse(summary["hazard_checked"])
        self.assertEqual(summary["total"], 7)
        self.assertEqual(summary["without_coordinates"], 2)
        self.assertIsNone(summary["any_hazard"])
        self.assertIsNone(summary["no_hazard"])
        self.assertIsNone(summary["by_category"])

    def test_全員が座標未取得なら_ハザード該当なしは0人(self):
        rows = [{"has_coordinates": False, "hazards": None} for _ in range(3)]
        summary = assignment.summarize_hazards(rows, ["津波"], True)
        self.assertEqual((summary["with_coordinates"], summary["without_coordinates"]), (0, 3))
        self.assertEqual((summary["any_hazard"], summary["no_hazard"]), (0, 0))


class ConstantsTest(unittest.TestCase):
    def test_大分類の並びとレビュー画面の色定義が一致する(self):
        review_builder = load_src_module("review/review_builder.py", "review_builder_for_test")
        hazard_loader = load_src_module("hazard/hazard_loader.py", "hazard_loader_for_test")
        self.assertEqual(list(review_builder.HAZARD_DISPLAY_STYLES), assignment.HAZARD_CATEGORY_ORDER)
        self.assertEqual(
            {hazard_loader.CATEGORY_TSUNAMI, hazard_loader.CATEGORY_STORM_SURGE,
             hazard_loader.CATEGORY_FLOOD, hazard_loader.CATEGORY_LANDSLIDE},
            set(assignment.HAZARD_CATEGORY_ORDER),
        )
        self.assertEqual(review_builder.CANDIDATE_COUNT, assignment.CANDIDATE_COUNT)


if __name__ == "__main__":
    unittest.main()

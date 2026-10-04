"""レビュー用HTML（review.html）の回帰テスト（完全な架空データ。外部通信なし）。

担当部署の業務要件（v1.1.0）に合わせて、画面に出すものと出さないものを確認する。
  * 上部のハザード集計（総件数・座標あり・座標未取得・いずれかに該当・ハザード該当なし・大分類別）
  * 本人住所のハザード、候補1〜3と直線距離
  * 道路に沿った参考経路・道路上の距離・避難所側のハザード・直線交差のハザード・避難所の災害種別対応は出さない

画面の見た目・操作そのもの（ブラウザでの表示）は自動テストの対象外で、実機確認として行う。

    python3 -m unittest discover -s test
"""
import copy
import unittest

import pandas as pd

import fake_data
from notebook_harness import NotebookRun

# 画面から無くなっていなければならない言葉（HTML・CSS・JavaScript全体）
FORBIDDEN_IN_HTML = [
    # 道路経路
    "road_route", "road-route", "ROUTES", "route-", "osm", "OpenStreetMap", "道路に沿った", "道路上", "道路経路",
    "経路を算出できません", "道路経路算出不可",
    # 避難所側のハザード・直線交差のハザード
    "shelter_in_hazard", "shelter_hazard_types", "straight_line", "避難所地点のハザード",
    "本人と避難所の間のハザード区域",
    # 避難所の災害種別対応
    "disaster_support", "disaster_types", "DISASTER_TYPES", "災害種別", "support-item",
    # 経路と誤認させる表現
    "避難経路",
]

# 画面に必要な言葉
REQUIRED_IN_HTML = [
    "本人住所のハザード集計", "総件数", "座標あり", "座標未取得", "いずれかに該当", "ハザード該当なし",
    "ハザード大分類別", "本人住所のハザード", "直線距離", "本人地点",
    "同じ人が複数のハザードに該当する場合は、それぞれに1件ずつ計上しています。",
    "各大分類の人数を合計しても「いずれかに該当」とは一致しない場合があります",
]


class ReviewHtmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_ = NotebookRun(
            fake_data.residents_csv_bytes(), fake_data.SHELTERS, fake_data.hazard_geojson_uploads()
        ).execute()
        cls.html = cls.run_.review_html()
        cls.data = cls.run_.review_data()

    @classmethod
    def tearDownClass(cls):
        cls.run_.close()

    def test_画面に必要な表示がある(self):
        for text in REQUIRED_IN_HTML:
            self.assertIn(text, self.html)

    def test_道路経路_避難所側ハザード_直線交差ハザード_災害種別対応の表示が無い(self):
        lowered = self.html
        for word in FORBIDDEN_IN_HTML:
            self.assertNotIn(word, lowered, f"レビュー画面に残っています: {word}")

    def test_集計値(self):
        summary = self.data["summary"]
        self.assertEqual(
            {k: summary[k] for k in ("total", "with_coordinates", "without_coordinates", "any_hazard", "no_hazard")},
            {"total": 7, "with_coordinates": 5, "without_coordinates": 2, "any_hazard": 4, "no_hazard": 1},
        )
        self.assertEqual({e["category"]: e["count"] for e in summary["by_category"]},
                         {"津波": 2, "高潮": 1, "洪水": 2, "土砂災害": 1})
        self.assertTrue(self.data["hazard_checked"])

    def test_要支援者ごとの本人ハザード(self):
        by_id = {r["resident_id"]: r for r in self.data["residents"]}
        r001 = by_id["R001"]
        self.assertTrue(r001["resident_in_hazard"])
        self.assertEqual([h["category"] for h in r001["hazards"]], ["津波", "高潮"])
        # 詳細区分は、大分類の部分を除いて個人の詳細表示用に保持する
        self.assertEqual(r001["hazards"][0]["details"], ["0.01m以上0.3m未満", "0.3m以上1.0m未満"])
        self.assertEqual(r001["hazards"][1]["details"], [])
        self.assertEqual(by_id["R007"]["hazards"][0]["details"], ["その他の河川"])
        self.assertEqual(by_id["R007"]["hazards"][1]["details"],
                         ["急傾斜地の崩壊:土砂災害警戒区域(指定済)"])
        self.assertEqual(by_id["R003"]["hazards"], [])
        self.assertFalse(by_id["R003"]["resident_in_hazard"])
        # 座標が使えない人は判定していない（「該当なし」ではない）
        for resident_id in ("R004", "R005"):
            self.assertIsNone(by_id[resident_id]["hazards"])
            self.assertIsNone(by_id[resident_id]["resident_in_hazard"])
            self.assertFalse(by_id[resident_id]["has_coordinates"])
            self.assertEqual(by_id[resident_id]["candidates"], [])

    def test_候補は1から3と直線距離だけを持つ(self):
        for resident in self.data["residents"]:
            self.assertLessEqual(len(resident["candidates"]), 3)
            for candidate in resident["candidates"]:
                self.assertEqual(set(candidate), {"rank", "name", "latitude", "longitude", "distance_m"})
        self.assertEqual(set(self.data["residents"][0]), {
            "resident_id", "address", "latitude", "longitude", "has_coordinates", "match_status",
            "resident_in_hazard", "hazards", "candidates"})
        self.assertNotIn("road_routes", self.data)
        self.assertNotIn("disaster_types", self.data)

    def test_ハザード区域の画像は大分類ごと(self):
        layers = {layer["label"]: layer for layer in self.data["hazard_layers"]}
        self.assertEqual(set(layers), {"津波", "高潮", "洪水", "土砂災害"})
        self.assertEqual({k: v["key"] for k, v in layers.items()},
                         {"津波": "tsunami", "高潮": "storm_surge", "洪水": "flood", "土砂災害": "landslide"})
        for resident in self.data["residents"]:
            for hazard in resident["hazards"] or []:
                self.assertIn(hazard["key"], {layer["key"] for layer in layers.values()})


    def test_作成日時は日本時間(self):
        # Google Colab の時計はUTC。UTCのまま表示すると、作成日時が9時間ずれる
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        generated_at = datetime.strptime(self.data["generated_at"], "%Y-%m-%d %H:%M")
        now_jst = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)
        self.assertLess(abs(now_jst - generated_at), timedelta(minutes=10))

    def test_背景地図の画像名は自治体によらない(self):
        self.assertEqual(self.data["basemap"]["image"], "assets/basemap.png")


class ReviewConsistencyCheckTest(unittest.TestCase):
    """レビューHTMLの内容が結果CSVと食い違うときは、HTMLを作らずに止まる。"""

    @classmethod
    def setUpClass(cls):
        cls.run_ = NotebookRun(
            fake_data.residents_csv_bytes(), fake_data.SHELTERS, fake_data.hazard_geojson_uploads()
        ).execute()
        cls.ns = cls.run_.namespace
        cls.builder = cls.ns["review_builder"]

    @classmethod
    def tearDownClass(cls):
        cls.run_.close()

    def build_data(self, final_df=None, review_rows=None, summary=None):
        ns = self.ns
        final_df = ns["final_df"] if final_df is None else final_df
        review_rows = ns["review_rows"] if review_rows is None else review_rows
        summary = ns["hazard_summary"] if summary is None else summary
        specs = self.builder.hazard_layer_specs(set(ns["hazard_gdf"]["hazard_category"]))
        data = self.builder.build_review_data(final_df, review_rows, [], specs, {"image": "x", "bounds": []}, summary)
        return data, final_df, review_rows

    def test_一致していれば通る(self):
        data, final_df, review_rows = self.build_data()
        self.builder.verify_review_data(data, final_df, review_rows)

    def test_集計が要支援者のデータと食い違えば止まる(self):
        summary = copy.deepcopy(self.ns["hazard_summary"])
        summary["by_category"][0]["count"] += 1
        data, final_df, review_rows = self.build_data(summary=summary)
        with self.assertRaises(RuntimeError) as caught:
            self.builder.verify_review_data(data, final_df, review_rows)
        self.assertIn("ハザード集計（津波）が一致しません", str(caught.exception))

    def test_ハザード該当なしに座標未取得を含めると止まる(self):
        summary = copy.deepcopy(self.ns["hazard_summary"])
        summary["no_hazard"] += summary["without_coordinates"]
        data, final_df, review_rows = self.build_data(summary=summary)
        with self.assertRaises(RuntimeError):
            self.builder.verify_review_data(data, final_df, review_rows)

    def test_候補がCSVと食い違えば止まる(self):
        final_df = self.ns["final_df"].copy()
        final_df.loc[0, "candidate_2"] = "別の避難所"
        data, _, review_rows = self.build_data()
        with self.assertRaises(RuntimeError) as caught:
            self.builder.verify_review_data(data, final_df, review_rows)
        self.assertIn("candidate_2が一致しません", str(caught.exception))

    def test_本人のハザード判定がCSVと食い違えば止まる(self):
        final_df = self.ns["final_df"].copy()
        final_df.loc[0, "resident_in_hazard"] = False
        data, _, review_rows = self.build_data()
        with self.assertRaises(RuntimeError):
            self.builder.verify_review_data(data, final_df, review_rows)

    def test_ハザードデータに大分類が無ければ作らない(self):
        area = self.ns["hazard_gdf"].drop(columns=["hazard_category"])
        with self.assertRaises(RuntimeError) as caught:
            self.builder.build_review_package(
                self.ns["final_df"], self.ns["review_rows"], area, self.ns["shelters_valid"],
                self.ns["hazard_summary"], self.run_.workdir / "out", self.ns["REVIEW_TEMPLATE_PATH"])
        self.assertIn("hazard_category", str(caught.exception))


class EmbedTest(unittest.TestCase):
    def test_住所などがHTMLとして解釈されない(self):
        from notebook_harness import load_src_module
        builder = load_src_module("review/review_builder.py", "review_builder_embed")
        text = builder.embed_review_json({"address": "</script><b>&"})
        self.assertNotIn("</script>", text)
        self.assertNotIn("<", text)
        self.assertNotIn(">", text)
        self.assertNotIn("&", text)

    def test_詳細区分は大分類の部分だけを除く(self):
        from notebook_harness import load_src_module
        builder = load_src_module("review/review_builder.py", "review_builder_detail")
        self.assertEqual(builder.hazard_detail("津波", "津波"), "")
        self.assertEqual(builder.hazard_detail("津波", "津波:0.3m以上1.0m未満"), "0.3m以上1.0m未満")
        self.assertEqual(builder.hazard_detail("洪水", "洪水（その他の河川）:計画規模"), "その他の河川:計画規模")
        self.assertEqual(builder.hazard_detail("洪水", "洪水（その他の河川）"), "その他の河川")
        self.assertEqual(builder.hazard_detail("土砂災害", "土砂災害:急傾斜地の崩壊:土砂災害警戒区域(指定済)"),
                         "急傾斜地の崩壊:土砂災害警戒区域(指定済)")
        # 大分類で始まらない表記は、そのまま詳細として保持する
        self.assertEqual(builder.hazard_detail("洪水", "独自の区分"), "独自の区分")


if __name__ == "__main__":
    unittest.main()

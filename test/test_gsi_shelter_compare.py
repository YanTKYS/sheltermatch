"""BODIKとGSIの指定緊急避難場所を比べる実験（experiments/gsi_shelter_compare/compare_logic.py）の回帰テスト。

比較ロジックだけを、完全な架空の避難所データで確認する。実在の避難所・外部サービスは使わず、
外部通信も行わない（通信を行う compare.py は import しない）。

    python3 -m unittest discover -s test
"""
import sys
import unittest
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).resolve().parents[1] / "experiments" / "gsi_shelter_compare"
sys.path.insert(0, str(EXPERIMENT_DIR))
import compare_logic as cl  # noqa: E402

# 架空の座標（東西に約0.001度 ≒ 約100m ずつ離す）
LAT, LON = 26.1000, 127.7000
DLON_10M = 0.0001      # 経度0.0001度 ≒ 約10m
DLON_100M = 0.001


def bodik(name, address, lat=LAT, lon=LON):
    return cl.Facility("bodik", name, address, lat, lon)


def gsi_feature(layer, name, address, lat=LAT, lon=LON, **properties):
    props = {"name": name, "address": address, "remarks": None, **properties}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def gsi_facility(layer, name, address, lat=LAT, lon=LON, scope="in_city", **properties):
    facility = cl.gsi_facility_from_feature(layer, gsi_feature(layer, name, address, lat, lon, **properties))
    facility.scope = scope
    return facility


class NormalizeTest(unittest.TestCase):
    def test_NFKCと空白とハイフンだけを整理する(self):
        self.assertEqual(cl.normalize_text("　架空小学校　体育館  "), "架空小学校 体育館")
        self.assertEqual(cl.normalize_text("架空町１－２－３"), "架空町1-2-3")
        self.assertEqual(cl.normalize_text("架空町1‐2−3"), "架空町1-2-3")
        self.assertEqual(cl.normalize_text(None), "")

    def test_語句の削除や意味的な変換はしない(self):
        self.assertNotEqual(cl.normalize_text("架空小学校"), cl.normalize_text("架空小"))
        self.assertNotEqual(cl.normalize_text("架空小学校グラウンド"), cl.normalize_text("架空小学校"))
        self.assertNotEqual(cl.normalize_text("沖縄県架空市字甲1"), cl.normalize_text("沖縄県架空市甲1"))

    def test_長音記号はハイフンとして扱わない(self):
        self.assertEqual(cl.normalize_text("架空センター"), "架空センター")

    def test_元の表記は保持する(self):
        facility = bodik("　架空Ａ公園", "架空町１－１")
        self.assertEqual(facility.original_name, "　架空Ａ公園")
        self.assertEqual(facility.normalized_name, "架空A公園")
        self.assertEqual(facility.original_address, "架空町１－１")


class TileTest(unittest.TestCase):
    def test_タイル番号とzoom10の既知の値(self):
        self.assertEqual(cl.tile_xy(26.1, 127.7, 10), (875, 435))

    def test_隣接タイルを含み重複しない(self):
        tiles = cl.tiles_for_points([(26.1, 127.7), (26.1001, 127.7001)], ring=1)
        self.assertEqual(len(tiles), 9)
        self.assertEqual(len(set(tiles)), 9)
        self.assertIn((875, 435), tiles)
        self.assertIn((874, 434), tiles)
        self.assertEqual(cl.tiles_for_points([(26.1, 127.7)], ring=0), [(875, 435)])


class MergeGsiTest(unittest.TestCase):
    def merge(self, *facilities):
        return cl.merge_gsi_layers(list(facilities))

    def test_同じ名称_住所_座標のレイヤー重複は統合しレイヤーを追跡する(self):
        merged, split = self.merge(
            gsi_facility("skhb01", "架空公園", "架空県架空市1-1", disaster1=1),
            gsi_facility("skhb05", "架空公園", "架空県架空市1-1", disaster5=1),
            gsi_facility("skhb03", "架空公園", "架空県架空市1-1", disaster3=1),
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].gsi_layers, ("skhb01", "skhb03", "skhb05"))
        self.assertEqual(merged[0].gsi_feature_count, 3)
        self.assertEqual(split, 0)
        self.assertEqual((merged[0].disasters["disaster1"], merged[0].disasters["disaster3"],
                          merged[0].disasters["disaster5"]), (1, 1, 1))

    def test_全角半角など正規化後に同じなら統合する(self):
        merged, _ = self.merge(
            gsi_facility("skhb01", "架空Ａ公園", "架空県架空市１－１"),
            gsi_facility("skhb02", "架空A公園", "架空県架空市1-1"),
        )
        self.assertEqual(len(merged), 1)
        self.assertTrue(any("元の表記が異なる" in n for n in merged[0].notes))

    def test_名称が同じでも住所が違えば統合しない(self):
        merged, _ = self.merge(
            gsi_facility("skhb01", "架空公園", "架空県架空市1-1"),
            gsi_facility("skhb01", "架空公園", "架空県架空市9-9"),
        )
        self.assertEqual(len(merged), 2)

    def test_名称と住所が同じでも座標が離れていれば統合しない(self):
        merged, split = self.merge(
            gsi_facility("skhb01", "架空公園", "架空県架空市1-1", lon=LON),
            gsi_facility("skhb05", "架空公園", "架空県架空市1-1", lon=LON + DLON_100M),
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(split, 1)

    def test_名称が空のものは統合しない(self):
        merged, _ = self.merge(
            gsi_facility("skhb01", "", "架空県架空市1-1"),
            gsi_facility("skhb05", "", "架空県架空市1-1"),
        )
        self.assertEqual(len(merged), 2)

    def test_統合したFeature間で属性が食い違えば注記する(self):
        merged, _ = self.merge(
            gsi_facility("skhb01", "架空公園", "架空県架空市1-1", disaster1=1),
            gsi_facility("skhb05", "架空公園", "架空県架空市1-1", disaster1=2),
        )
        self.assertTrue(any("disaster1" in n for n in merged[0].notes))

    def test_Point以外のFeatureは読み飛ばす(self):
        feature = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}, "properties": {}}
        self.assertIsNone(cl.gsi_facility_from_feature("skhb01", feature))


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.bodik = [bodik("架空公園", "架空県架空市1-1")]

    def scope_of(self, facility):
        return cl.assign_gsi_scope([facility], self.bodik, "架空市")[0].scope

    def test_住所に市名があれば対象(self):
        self.assertEqual(self.scope_of(gsi_facility("skhb01", "A", "架空県架空市2-2")), "in_city")

    def test_住所が空でも近くなら対象で_遠ければ対象外(self):
        near = gsi_facility("skhb01", "A", "", lon=LON + DLON_100M)
        far = gsi_facility("skhb01", "A", "", lat=LAT + 1.0)
        self.assertEqual(self.scope_of(near), "address_missing_near")
        self.assertEqual(self.scope_of(far), "excluded_address_missing_far")

    def test_住所に市名が無くてもBODIKと同名や近接なら対象にする(self):
        same_name = gsi_facility("skhb01", "架空公園", "架空県別市1-1", lat=LAT + 1.0)
        close = gsi_facility("skhb01", "別の施設", "架空県別市5-5", lon=LON + DLON_10M)
        other = gsi_facility("skhb01", "別の施設", "架空県別市5-5", lat=LAT + 1.0)
        self.assertEqual(self.scope_of(same_name), "other_address_near_bodik")
        self.assertEqual(self.scope_of(close), "other_address_near_bodik")
        self.assertEqual(self.scope_of(other), "excluded_other_address")


class CompareTest(unittest.TestCase):
    def rows_by_status(self, bodik_list, gsi_list):
        rows = cl.compare_facilities(bodik_list, gsi_list)
        by_status = {}
        for row in rows:
            by_status.setdefault(row.status, []).append(row)
        return by_status

    def test_名称_住所_座標が一致すればexact_matchで距離を計算する(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空公園", "架空県架空市1-1", lon=LON + DLON_10M)],
        )
        self.assertEqual(list(by), ["exact_match"])
        self.assertAlmostEqual(by["exact_match"][0].distance_m, 10.0, delta=1.5)

    def test_全角半角の表記差だけならexact_match(self):
        by = self.rows_by_status(
            [bodik("架空Ａ公園", "架空県架空市１－１")],
            [gsi_facility("skhb01", "架空A公園", "架空県架空市1-1")],
        )
        self.assertEqual(list(by), ["exact_match"])

    def test_相手が無ければbodik_onlyとgsi_onlyで_最寄りは参考情報として注記する(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市1-1")],
            [gsi_facility("skhb01", "まったく別の施設", "架空県架空市9-9", lat=LAT + 0.1)],
        )
        self.assertEqual(sorted(by), ["bodik_only", "gsi_only"])
        self.assertIn("同一施設とは判断していない", by["bodik_only"][0].notes[0])
        self.assertIsNone(by["bodik_only"][0].distance_m)

    def test_名称は同じで住所が違えばreview_needed(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空公園", "架空県架空市2-2")],
        )
        self.assertEqual(list(by), ["review_needed"])
        self.assertIn("住所が異なる", by["review_needed"][0].reason)

    def test_字の有無のような表記差は同一視せずreview_needed(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市糸満1")],
            [gsi_facility("skhb01", "架空公園", "架空県架空市字糸満1")],
        )
        self.assertEqual(list(by), ["review_needed"])

    def test_名称は同じで住所が欠損していればreview_needed(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空公園", "", scope="address_missing_near")],
        )
        self.assertEqual(list(by), ["review_needed"])
        self.assertTrue(any("住所が空欄" in n for n in by["review_needed"][0].notes))

    def test_名称と住所は同じでも座標差が大きければreview_needed(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空公園", "架空県架空市1-1", lon=LON + DLON_100M)],
        )
        self.assertEqual(list(by), ["review_needed"])
        self.assertGreater(by["review_needed"][0].distance_m, cl.EXACT_MAX_DISTANCE_M)

    def test_住所は同じで名称が違えばreview_needed(self):
        by = self.rows_by_status(
            [bodik("架空小学校グラウンド", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空小学校", "架空県架空市1-1")],
        )
        self.assertEqual(list(by), ["review_needed"])
        self.assertIn("名称が異なる", by["review_needed"][0].reason)

    def test_名称も住所も違うが近接していればreview_needed(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空広場", "架空県架空市2-2", lon=LON + DLON_10M)],
        )
        self.assertEqual(list(by), ["review_needed"])
        self.assertIn("近接", by["review_needed"][0].reason)

    def test_名称も住所も違い離れていれば対応付けない(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空広場", "架空県架空市2-2", lon=LON + DLON_100M * 5)],
        )
        self.assertEqual(sorted(by), ["bodik_only", "gsi_only"])

    def test_相手が複数あって同一と断定できなければexact_matchにしない(self):
        by = self.rows_by_status(
            [bodik("架空公園", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空公園", "架空県架空市1-1", lon=LON),
             gsi_facility("skhb01", "架空公園", "架空県架空市1-1", lon=LON + DLON_10M)],
        )
        self.assertNotIn("exact_match", by)
        self.assertEqual(len(by["review_needed"]), 2)
        self.assertNotIn("bodik_only", by)

    def test_1つのGSI施設に複数のBODIK施設が対応するときも片方をbodik_onlyにしない(self):
        by = self.rows_by_status(
            [bodik("架空小学校グラウンド", "架空県架空市1-1"), bodik("架空小学校校舎", "架空県架空市1-1")],
            [gsi_facility("skhb01", "架空小学校", "架空県架空市1-1")],
        )
        self.assertEqual(list(by), ["review_needed"])
        self.assertEqual(len(by["review_needed"]), 2)
        self.assertEqual(sum(1 for r in by["review_needed"] if any("1対多" in n for n in r.notes)), 1)

    def test_exact_matchは1対1で各施設は1度だけ現れる(self):
        bodik_list = [bodik("公園A", "架空県架空市1-1"), bodik("公園B", "架空県架空市2-2", lon=LON + DLON_100M * 3),
                      bodik("公園C", "架空県架空市3-3", lon=LON + DLON_100M * 6)]
        gsi_list = [gsi_facility("skhb01", "公園A", "架空県架空市1-1"),
                    gsi_facility("skhb05", "公園B", "架空県架空市2-2", lon=LON + DLON_100M * 3),
                    gsi_facility("skhb01", "公園D", "架空県架空市4-4", lat=LAT + 0.1)]
        rows = cl.compare_facilities(bodik_list, gsi_list)
        counts = cl.summarize(rows)["counts"]
        self.assertEqual(counts, {"exact_match": 2, "review_needed": 0, "bodik_only": 1, "gsi_only": 1})
        exact_bodik = [r.bodik.original_name for r in rows if r.status == "exact_match"]
        self.assertEqual(sorted(exact_bodik), ["公園A", "公園B"])

    def test_gsi_layersを比較結果に引き継ぐ(self):
        merged, _ = cl.merge_gsi_layers([gsi_facility("skhb01", "架空公園", "架空県架空市1-1"),
                                         gsi_facility("skhb05", "架空公園", "架空県架空市1-1")])
        rows = cl.compare_facilities([bodik("架空公園", "架空県架空市1-1")], merged)
        self.assertEqual(";".join(rows[0].gsi.gsi_layers), "skhb01;skhb05")


class StatsTest(unittest.TestCase):
    def test_距離の最大_中央値_平均と閾値超の件数(self):
        stats = cl.distance_stats([0, 5, 20, 50, 150, None])
        self.assertEqual(stats["count"], 5)
        self.assertEqual(stats["max"], 150)
        self.assertEqual(stats["median"], 20)
        self.assertEqual(stats["mean"], 45)
        self.assertEqual(stats["over"], {10: 3, 30: 2, 100: 1})
        self.assertIsNone(cl.distance_stats([None]))

    def test_測地線距離の計算(self):
        a, b = bodik("A", ""), bodik("B", "", lon=LON + DLON_100M)
        self.assertAlmostEqual(cl.distance_m(a, b), 100.0, delta=2.0)
        self.assertIsNone(cl.distance_m(a, bodik("C", "", lat=None, lon=None)))

    def test_座標の不正値は欠損として扱う(self):
        self.assertEqual(cl.parse_coordinate("", ""), (None, None))
        self.assertEqual(cl.parse_coordinate("abc", "127.7"), (None, None))
        self.assertEqual(cl.parse_coordinate("0", "0"), (None, None))
        self.assertEqual(cl.parse_coordinate("26.1", "127.7"), (26.1, 127.7))

    def test_表記差の集計は元の文字列で比べる(self):
        rows = cl.compare_facilities(
            [bodik("架空Ａ公園", "架空県架空市1-1"), bodik("架空B公園", "架空県架空市2-2", lon=LON + DLON_100M * 3)],
            [gsi_facility("skhb01", "架空A公園", "架空県架空市1-1"),
             gsi_facility("skhb01", "架空B公園", "架空県架空市2-2", lon=LON + DLON_100M * 3)])
        summary = cl.summarize(rows)
        self.assertEqual(summary["exact_name"], {"identical": 1, "identical_after_normalization_only": 1,
                                                 "different": 0})
        self.assertEqual(summary["exact_address"]["identical"], 2)


if __name__ == "__main__":
    unittest.main()

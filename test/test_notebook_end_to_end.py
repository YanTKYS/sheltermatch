"""sheltermatch.ipynb を、外部通信なしで最後まで実行する回帰テスト（完全な架空データ）。

Notebook のセルは書き換えず、取得先の版（SHELTERMATCH_CODE_REF=v1.1.0）も変えずに、
GitHub からの取得だけを作業ツリーの src/ へ差し替えて実行する（test/notebook_harness.py）。
新しい src/ と Notebook の組み合わせで、結果CSV・レビューZIP・集計が揃うことを確認する。

    python3 -m unittest discover -s test
"""
import csv
import io
import json
import re
import sys
import unittest

import fake_data
from notebook_harness import NotebookRun, code_cells

EXPECTED_COLUMNS = [
    "resident_id", "address", "latitude", "longitude", "geocode_status", "match_status",
    "resident_in_hazard", "resident_hazard_types",
    "candidate_1", "distance_1_m", "candidate_2", "distance_2_m", "candidate_3", "distance_3_m",
]


def read_result_rows(run):
    return list(csv.DictReader(io.StringIO(run.result_csv_text())))


class NotebookEndToEndWithHazardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_ = NotebookRun(
            fake_data.residents_csv_bytes(), fake_data.SHELTERS, fake_data.hazard_geojson_uploads()
        ).execute()
        cls.rows = read_result_rows(cls.run_)
        cls.data = cls.run_.review_data()

    @classmethod
    def tearDownClass(cls):
        cls.run_.close()

    def test_結果CSVは正式な14列に入力の独自列を加えたもの(self):
        header = self.run_.result_csv_text().splitlines()[0].split(",")
        self.assertEqual(header, EXPECTED_COLUMNS + ["note"])

    def test_座標が使えない行も削除されない(self):
        self.assertEqual(len(self.rows), len(fake_data.RESIDENTS))
        by_id = {row["resident_id"]: row for row in self.rows}
        for resident_id, status in (("R004", "no_coordinates"), ("R005", "invalid_coordinates")):
            row = by_id[resident_id]
            self.assertEqual(row["match_status"], status)
            for column in ("resident_in_hazard", "resident_hazard_types", "candidate_1", "distance_1_m",
                           "candidate_2", "distance_2_m", "candidate_3", "distance_3_m"):
                self.assertEqual(row[column], "", f"{resident_id}の{column}が空欄ではありません")

    def test_本人住所のハザードと候補(self):
        by_id = {row["resident_id"]: row for row in self.rows}
        self.assertEqual(by_id["R001"]["resident_in_hazard"], "True")
        self.assertEqual(by_id["R003"]["resident_in_hazard"], "False")
        self.assertEqual(by_id["R001"]["candidate_1"], "架空避難所A")
        self.assertEqual(by_id["R001"]["distance_1_m"], "0.0")

    def test_集計(self):
        summary = self.data["summary"]
        self.assertEqual(summary["total"], 7)
        self.assertEqual(summary["with_coordinates"], 5)
        self.assertEqual(summary["without_coordinates"], 2)
        self.assertEqual(summary["any_hazard"], 4)
        self.assertEqual(summary["no_hazard"], 1)
        self.assertEqual({e["category"]: e["count"] for e in summary["by_category"]},
                         {"津波": 2, "高潮": 1, "洪水": 2, "土砂災害": 1})

    def test_集計はNotebookの表示にも出る(self):
        text = self.run_.output.getvalue()
        for expected in ("総件数", "座標あり", "座標未取得", "いずれかに該当", "ハザード該当なし",
                         "津波", "高潮", "洪水", "土砂災害", "それぞれに1件ずつ計上しています"):
            self.assertIn(expected, text)

    def test_レビューZIPの中身(self):
        with self.run_.review_zip() as archive:
            names = archive.namelist()
        self.assertIn("sheltermatch_review/review.html", names)
        self.assertTrue(any(n.startswith("sheltermatch_review/assets/hazard_") for n in names))
        self.assertIn("sheltermatch_review/assets/basemap_itoman.png", names)
        # 道路経路・OpenStreetMapの道路データに関するファイルは含めない
        self.assertFalse([n for n in names if "osm" in n.lower() or "road" in n.lower()])

    def test_外部通信はGitHubの取得とBODIK_APIだけ_道路データは取得しない(self):
        for url in self.run_.urls:
            self.assertTrue(
                url.startswith("https://raw.githubusercontent.com/YanTKYS/sheltermatch/v1.1.0/src/")
                or url.startswith("https://data.bodik.jp/api/action/datastore_search"),
                url,
            )
        self.assertFalse([u for u in self.run_.urls if "overpass" in u or "nominatim" in u or "osm" in u.lower()])
        self.assertNotIn("osmnx", sys.modules)
        # 取得する外部モジュール（assignmentを含む4つ）
        fetched = sorted(u.split("/src/", 1)[1] for u in self.run_.urls if "/src/" in u)
        self.assertEqual(fetched, sorted([
            "hazard/hazard_loader.py", "assignment/shelter_assignment.py",
            "review/review_builder.py", "review/review_template.html"]))

    def test_v1_1_0のsrcを取得する(self):
        self.assertEqual(self.run_.namespace["SHELTERMATCH_CODE_REF"], "v1.1.0")

    def test_ダウンロードされるのはCSVとZIP(self):
        self.assertEqual(self.run_.files.downloads, ["assigned_shelters.csv", "sheltermatch_review.zip"])

    def test_レビューHTMLの候補はCSVと一致する(self):
        by_id = {resident["resident_id"]: resident for resident in self.data["residents"]}
        for row in self.rows:
            resident = by_id[row["resident_id"]]
            names = [c["name"] for c in resident["candidates"]]
            expected = [row[f"candidate_{n}"] for n in (1, 2, 3) if row[f"candidate_{n}"]]
            self.assertEqual(names, expected)
            distances = [c["distance_m"] for c in resident["candidates"]]
            self.assertEqual(distances, [float(row[f"distance_{n}_m"]) for n in (1, 2, 3) if row[f"distance_{n}_m"]])


class NotebookEndToEndWithoutHazardTest(unittest.TestCase):
    def test_ハザード判定を行わない通常実行(self):
        with NotebookRun(fake_data.residents_csv_bytes(), fake_data.SHELTERS).execute() as run:
            header = run.result_csv_text().splitlines()[0].split(",")
            self.assertEqual(header, EXPECTED_COLUMNS + ["note"])
            rows = read_result_rows(run)
            self.assertTrue(all(row["resident_in_hazard"] == "" and row["resident_hazard_types"] == ""
                                for row in rows))
            self.assertEqual(rows[0]["candidate_1"], "架空避難所A")
            data = run.review_data()
            self.assertFalse(data["hazard_checked"])
            self.assertEqual(data["hazard_layers"], [])
            self.assertIsNone(data["summary"]["by_category"])
            self.assertEqual(data["summary"]["without_coordinates"], 2)
            with run.review_zip() as archive:
                self.assertFalse([n for n in archive.namelist() if "hazard_" in n])


class NotebookSourceTest(unittest.TestCase):
    """Notebook のコードセルに、道路経路・災害種別・可変の候補数が残っていないこと。"""

    def test_道路経路に関する記述が無い(self):
        source = "\n".join(code_cells())
        for word in ("ENABLE_ROAD_ROUTES", "osmnx", "road_routes", "OpenStreetMap", "道路経路", "道路に沿った"):
            self.assertNotIn(word, source)

    def test_災害種別対応に関する記述が無い(self):
        source = "\n".join(code_cells())
        for word in ("災害種別_", "disaster_support", "_disaster_support", "DISASTER"):
            self.assertNotIn(word, source)

    def test_候補数は設定で変えられない(self):
        source = "\n".join(code_cells())
        self.assertIsNone(re.search(r"^TOP_N\s*=", source, re.M))
        self.assertNotIn("TOP_N", source)

    def test_利用者設定は2項目(self):
        settings = [c for c in code_cells() if c.startswith("# ===== 利用者設定")][0]
        names = re.findall(r"^([A-Z_]+) = ", settings, re.M)
        self.assertEqual(names, ["ENABLE_HAZARD_CHECK", "SHELTER_SOURCE"])


if __name__ == "__main__":
    unittest.main()

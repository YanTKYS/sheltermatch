"""sheltermatch.ipynb を、外部通信なしで最後まで実行する回帰テスト（完全な架空データ）。

Notebook のセルは書き換えず、取得先の版（SHELTERMATCH_CODE_REF=v1.2.1）も変えずに、
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
from pathlib import Path

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
        self.assertIn("sheltermatch_review/assets/basemap.png", names)
        # 道路経路・OpenStreetMapの道路データに関するファイルは含めない
        self.assertFalse([n for n in names if "osm" in n.lower() or "road" in n.lower()])

    def test_レビューZIPに自作部分のMITと第三者通知を同梱する(self):
        # リポジトリ直下のLICENSE（正式なMIT本文）と同じ内容を、ZIPにも入れる
        repo_license = (Path(__file__).resolve().parent.parent / "LICENSE").read_text(encoding="utf-8")
        with self.run_.review_zip() as archive:
            names = archive.namelist()
            self.assertIn("sheltermatch_review/LICENSE.txt", names)
            self.assertIn("sheltermatch_review/THIRD_PARTY_NOTICES.txt", names)
            self.assertEqual(archive.read("sheltermatch_review/LICENSE.txt").decode("utf-8"), repo_license)
            notices = archive.read("sheltermatch_review/THIRD_PARTY_NOTICES.txt").decode("utf-8")
        for expected in ("MIT License", "Leaflet", "assets/leaflet-LICENSE.txt", "gsi-basemap-NOTICE.txt",
                         "MIT Licenseの対象外"):
            self.assertIn(expected, notices)

    def test_BODIK_APIで取得した糸満市のデータには出典とCC_BY_4_0を付ける(self):
        with self.run_.review_zip() as archive:
            notices = archive.read("sheltermatch_review/THIRD_PARTY_NOTICES.txt").decode("utf-8")
        for expected in (
            "糸満市", "指定緊急避難場所一覧", "https://data.bodik.jp/dataset/472107_evacuation_space",
            "クリエイティブ・コモンズ 表示 4.0 国際（CC BY 4.0）", "https://creativecommons.org/licenses/by/4.0/",
            "名称・緯度・経度の列だけを取り出し", "直線距離を計算して",
        ):
            self.assertIn(expected, notices)

    def test_国土地理院の通知に加工した旨を書く(self):
        from notebook_harness import load_src_module
        builder = load_src_module("review/review_builder.py", "review_builder_gsi_notice")
        text = builder.GSI_BASEMAP_NOTICE_TEXT
        for expected in ("国土地理院", "https://maps.gsi.go.jp/", builder.GSI_TERMS_URL, "結合", "加工"):
            self.assertIn(expected, text)

    def test_既存の第三者通知は維持される(self):
        # 実際の取得処理は差し替えて動かさないため、同梱するファイルの定義と、背景地図のNOTICEの文面で確認する
        builder = self.run_.namespace["review_builder"]
        self.assertIn("leaflet-LICENSE.txt", [package_path for _, package_path, _ in builder.LEAFLET_FILES])
        self.assertEqual(builder.LEAFLET_VERSION, "1.9.4")
        self.assertEqual(builder.GSI_ATTRIBUTION_TEXT, "国土地理院")

    def test_外部通信はGitHubの取得とBODIK_APIだけ_道路データは取得しない(self):
        for url in self.run_.urls:
            self.assertTrue(
                url.startswith("https://raw.githubusercontent.com/YanTKYS/sheltermatch/v1.2.1/src/")
                or url == "https://raw.githubusercontent.com/YanTKYS/sheltermatch/v1.2.1/configs/itoman-city.json"
                or url.startswith("https://data.bodik.jp/api/action/datastore_search"),
                url,
            )
        self.assertFalse([u for u in self.run_.urls if "overpass" in u or "nominatim" in u or "osm" in u.lower()])
        self.assertNotIn("osmnx", sys.modules)
        # 取得する外部モジュール（assignmentと設定の読込を含む5つ）と、自治体の設定ファイル（モジュールと同じ版）
        fetched = sorted(u.split("/src/", 1)[1] for u in self.run_.urls if "/src/" in u)
        self.assertEqual(fetched, sorted([
            "hazard/hazard_loader.py", "assignment/shelter_assignment.py", "config/municipality_config.py",
            "review/review_builder.py", "review/review_template.html"]))
        configs = [u for u in self.run_.urls if "/configs/" in u]
        self.assertEqual(configs, ["https://raw.githubusercontent.com/YanTKYS/sheltermatch/v1.2.1/configs/itoman-city.json"])

    def test_v1_2_1のsrcと設定ファイルを取得する(self):
        self.assertEqual(self.run_.namespace["SHELTERMATCH_CODE_REF"], "v1.2.1")
        # 外部モジュールも設定ファイルも、同じ版（タグ）から取得する（mainとの混在なし）
        for url in self.run_.urls:
            if url.startswith("https://raw.githubusercontent.com/"):
                self.assertTrue(url.startswith("https://raw.githubusercontent.com/YanTKYS/sheltermatch/v1.2.1/"), url)

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


def shelters_csv_bytes():
    """持ち込みの避難所一覧CSV（架空。自治体標準ODSの列名）。"""
    lines = ["名称,緯度,経度"] + [f"{s['名称']},{s['緯度']},{s['経度']}" for s in fake_data.SHELTERS]
    return ("\n".join(lines) + "\n").encode("utf-8")


class ShelterDataNoticeTest(unittest.TestCase):
    """避難所データの出典は、実際にそのデータを取得したときだけ付ける（別のデータへ誤って付けない）。"""

    # 糸満市のデータの出典・ライセンスを表す文言（別のデータの通知に含まれてはならない）
    ITOMAN_WORDS = ("糸満市", "472107_evacuation_space", "CC BY 4.0", "クリエイティブ・コモンズ",
                    "creativecommons.org", "3132a0a4")

    def notices_of(self, run):
        with run.review_zip() as archive:
            return archive.read("sheltermatch_review/THIRD_PARTY_NOTICES.txt").decode("utf-8")

    def assert_no_itoman_attribution(self, notices):
        for word in self.ITOMAN_WORDS:
            self.assertNotIn(word, notices)

    def test_CSVを指定したときは糸満市の出典を付けない(self):
        run = NotebookRun(fake_data.residents_csv_bytes(), fake_data.SHELTERS)
        run.shelters_csv = shelters_csv_bytes()
        with run.execute(settings={"SHELTER_SOURCE": '"csv"'}):
            notices = self.notices_of(run)
            self.assertFalse([u for u in run.urls if u.startswith("https://data.bodik.jp/api/")])
        self.assert_no_itoman_attribution(notices)
        self.assertIn("利用者が用意したCSV", notices)

    def test_API取得に失敗してCSVへ切り替えたときも糸満市の出典を付けない(self):
        run = NotebookRun(fake_data.residents_csv_bytes(), fake_data.SHELTERS)
        run.bodik_fails = True
        run.shelters_csv = shelters_csv_bytes()
        with run.execute():
            notices = self.notices_of(run)
            self.assertIn("BODIK APIから避難所一覧を取得できませんでした", run.output.getvalue())
        self.assert_no_itoman_attribution(notices)
        self.assertIn("利用者が用意したCSV", notices)

    def test_出典を確認していないデータセットと出どころ不明には出典を付けない(self):
        from notebook_harness import load_src_module
        builder = load_src_module("review/review_builder.py", "review_builder_origin")
        unknown_dataset = {"kind": "bodik_api", "resource_id": "00000000-0000-0000-0000-000000000000"}
        for origin in (unknown_dataset, {"kind": "csv"}, {}, None, "bodik_api"):
            text = builder.build_third_party_notices(origin)
            self.assert_no_itoman_attribution(text)
            self.assertIn("MIT License", text)
        self.assertIn("出典・ライセンスを、BODIKの公式ページで確認", builder.build_third_party_notices(unknown_dataset))
        # 出典を確認済みのデータセットには付く
        known = builder.build_third_party_notices({"kind": "bodik_api", "resource_id": "3132a0a4-f522-4b2d-bf18-f106d8b3a5ae"})
        self.assertIn("CC BY 4.0", known)
        # CSVの種類で、既知のresource_idが入っていても、BODIK APIでなければ出典は付けない
        self.assert_no_itoman_attribution(builder.build_third_party_notices(
            {"kind": "csv", "resource_id": "3132a0a4-f522-4b2d-bf18-f106d8b3a5ae"}))

    def test_避難所の出どころの記録は結果CSVや候補に影響しない(self):
        with NotebookRun(fake_data.residents_csv_bytes(), fake_data.SHELTERS).execute() as api_run:
            api_csv = api_run.result_csv_text()
        run = NotebookRun(fake_data.residents_csv_bytes(), fake_data.SHELTERS)
        run.shelters_csv = shelters_csv_bytes()
        with run.execute(settings={"SHELTER_SOURCE": '"csv"'}):
            self.assertEqual(run.result_csv_text(), api_csv)


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

    def test_利用者設定は設定名と2項目だけ(self):
        # 自治体の切替は CONFIG_NAME だけ。ほかの2項目は既定では None（設定ファイルの値に従う）で、
        # 値を書き込んだときだけ設定ファイルの値より優先される
        settings = [c for c in code_cells() if c.startswith("# ===== 利用者設定")][0]
        names = re.findall(r"^([A-Z_]+) = ", settings, re.M)
        self.assertEqual(names, ["CONFIG_NAME", "ENABLE_HAZARD_CHECK", "SHELTER_SOURCE", "NOTEBOOK_SETTINGS"])
        self.assertIn('CONFIG_NAME = "itoman-city"', settings)
        self.assertIn("ENABLE_HAZARD_CHECK = None", settings)
        self.assertIn("SHELTER_SOURCE = None", settings)


if __name__ == "__main__":
    unittest.main()

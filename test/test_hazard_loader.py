"""ハザードデータの読込（src/hazard/hazard_loader.py）のうち、大分類（hazard_category）に関する回帰テスト。

ハザード別の人数集計は、読込時に付ける大分類 hazard_category で行う（hazard_type の表示文字列を
後から解析しない）。ここでは、大分類が公式データの配布形式から決まり、詳細区分（hazard_type）が
従来どおり保持されることを、完全な架空のポリゴンで確認する。外部通信は行わない。

    python3 -m unittest discover -s test
"""
import contextlib
import io
import unittest
import zipfile

import fake_data
from notebook_harness import load_src_module

hazard_loader = load_src_module("hazard/hazard_loader.py", "hazard_loader_under_test")


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class HazardCategoryTest(unittest.TestCase):
    def test_APIバージョン(self):
        self.assertEqual(hazard_loader.HAZARD_LOADER_API_VERSION, 2)

    def test_読み込んだレイヤーは大分類と詳細区分とジオメトリを持つ(self):
        layer = quiet(hazard_loader.load_uploaded_hazards, fake_data.hazard_geojson_uploads())
        self.assertEqual(list(layer.columns), ["hazard_category", "hazard_type", "geometry"])
        by_category = {c: sorted(layer.loc[layer["hazard_category"] == c, "hazard_type"].unique())
                       for c in layer["hazard_category"].unique()}
        self.assertEqual(by_category, {
            "津波": ["津波:0.01m以上0.3m未満", "津波:0.3m以上1.0m未満"],
            "高潮": ["高潮"],
            "洪水": ["洪水（その他の河川）"],
            "土砂災害": ["土砂災害:急傾斜地の崩壊:土砂災害警戒区域(指定済)"],
        })

    def test_洪水は河川区分が違っても大分類は洪水(self):
        geojson = fake_data.hazard_geojson_uploads()["A31a-25_47_20_GEOJSON.geojson"]
        uploads = {
            "A31a-25_47_10_GEOJSON.geojson": geojson,
            "A31a-25_47_20_GEOJSON.geojson": geojson,
        }
        layer = quiet(hazard_loader.load_uploaded_hazards, uploads)
        self.assertEqual(set(layer["hazard_category"]), {"洪水"})
        self.assertEqual(set(layer["hazard_type"]),
                         {"洪水（洪水予報河川・水位周知河川）", "洪水（その他の河川）"})

    def test_ZIPのサブフォルダは詳細区分になり_大分類は変わらない(self):
        geojson = fake_data.hazard_geojson_uploads()["A31a-25_47_20_GEOJSON.geojson"]
        archive = zip_bytes({"計画規模/area.geojson": geojson, "想定最大規模/area.geojson": geojson})
        layer = quiet(hazard_loader.load_uploaded_hazards, {"A31a-25_47_10_GEOJSON.zip": archive})
        self.assertEqual(set(layer["hazard_category"]), {"洪水"})
        self.assertEqual(set(layer["hazard_type"]), {
            "洪水（洪水予報河川・水位周知河川）:計画規模", "洪水（洪水予報河川・水位周知河川）:想定最大規模"})

    def test_自動判定できないファイルは入力した種別名が大分類になる(self):
        geojson = fake_data.hazard_geojson_uploads()["47007_takasiosinnsuisoutei_fake.geojson"]
        layer = quiet(hazard_loader.load_uploaded_hazards, {"独自の区域.geojson": geojson},
                      ask_hazard_type=lambda filename: "洪水")
        self.assertEqual(set(layer["hazard_category"]), {"洪水"})
        # 入力が空欄ならファイル名をそのまま使う（従来どおり）
        layer = quiet(hazard_loader.load_uploaded_hazards, {"独自の区域.geojson": geojson})
        self.assertEqual(set(layer["hazard_category"]), {"独自の区域.geojson"})

    def test_ファイル名の判定は大分類と基本表記を返す(self):
        detected = hazard_loader.detect_hazard("A33-25_47_GEOJSON(1).zip")
        self.assertEqual((detected.category, detected.label), ("土砂災害", "土砂災害"))
        detected = hazard_loader.detect_hazard("A31a-25_47_10_GEOJSON.zip")
        self.assertEqual((detected.category, detected.label), ("洪水", "洪水（洪水予報河川・水位周知河川）"))
        self.assertEqual(hazard_loader.detect_hazard("level7_x.zip").category, "津波")
        self.assertEqual(hazard_loader.detect_hazard("47007_takasiosinnsuisoutei_22itoman.zip").category, "高潮")
        self.assertIsNone(hazard_loader.detect_hazard("unknown.zip"))

    def test_壊れたZIPは_ほかのファイルとあわせて読み込めなかったファイルとして表示して止まる(self):
        # ダウンロードが途中で切れたZIP等。生の例外（BadZipFile）で止まらず、どのファイルが読めなかったかを示す
        uploads = dict(fake_data.hazard_geojson_uploads())
        uploads["A33-25_47_GEOJSON.zip"] = zip_bytes({"area.geojson": b"{}"})[:40]
        with self.assertRaises(RuntimeError) as raised:
            quiet(hazard_loader.load_uploaded_hazards, uploads)
        message = str(raised.exception)
        self.assertIn("A33-25_47_GEOJSON.zip", message)
        self.assertIn("ZIPファイルとして開けませんでした", message)

    def test_空のレイヤーにも大分類の列がある(self):
        self.assertIn("hazard_category", hazard_loader.empty_hazard_layer().columns)


if __name__ == "__main__":
    unittest.main()
